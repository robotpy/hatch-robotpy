import functools
import os
import pathlib
import posixpath
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import typing as T

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from packaging.markers import Marker

from .config import HookConfig, Download
from .download import download_file, extract_zip
from .maven import convert_maven_to_downloads
from .platforms import get_platform
from .validate import parse_input


class DownloadHook(BuildHookInterface):
    PLUGIN_NAME = "robotpy"

    def initialize(self, version: str, build_data: T.Dict[str, T.Any]) -> None:
        # Don't need to generate files when creating an sdist
        # - this violates the idea that an sdist should be able to be used
        #   offline, but because we're downloading the external artifacts
        #   we don't expect that to be a usecase anyways
        #   TODO: add support for getting files from local maven repository?
        if self.target_name != "wheel":
            return

        build_data["pure_python"] = False

        root = pathlib.Path(self.root)

        # Ensure that we only delete paths once
        self.cleaned_paths: T.Set[pathlib.Path] = set()

        self.setup_cache()
        try:

            for download in self.downloads:
                lib_map = self.make_lib_map(download)
                downloaded = self.download(download, lib_map.copy())

                # strip bin
                strip = download.strip
                if strip is None:
                    if version == "editable":
                        strip = False
                    else:
                        strip = True

                if strip and self.target_name == "wheel":
                    if self.platform.os == "linux":
                        for lib in lib_map.values():
                            self.strip(lib)

                build_data["artifacts"] += [
                    p.relative_to(root).as_posix() for p in downloaded
                ]

        finally:
            self.cleanup_cache()

        # Setup the wheel tag
        if "tag" not in build_data:
            build_data["tag"] = f"py3-none-{self.platform.tag}"

    @functools.cached_property
    def parsed_cfg(self):
        return parse_input(
            self.config, HookConfig, "pyproject.toml", "tool.hatch.build.hooks.robotpy."
        )

    @functools.cached_property
    def platform(self):
        return get_platform()

    @functools.cached_property
    def downloads(self):

        downloads = self.parsed_cfg.download[:]

        for mcfg in self.parsed_cfg.maven_lib_download:
            if mcfg.enable_if and not Marker(mcfg.enable_if).evaluate():
                what = f"{mcfg.group_id}.{mcfg.artifact_id}"
                self.app.display_info(
                    f"{what} skipped because enable_if did not match current environment"
                )
                continue

            dls = convert_maven_to_downloads(mcfg)
            downloads.extend(dls)

        for download in downloads:
            if download.enable_if and not Marker(download.enable_if).evaluate():
                self.app.display_info(
                    f"{download.url} skipped because enable_if did not match current environment"
                )
                continue

            download._update_with_platform(self.platform)

        return downloads

    def setup_cache(self):
        if "HATCH_ROBOTPY_CACHE" in os.environ:
            self.cache = pathlib.Path(os.environ["HATCH_ROBOTPY_CACHE"])
            self.cache.mkdir(parents=True, exist_ok=True)
        else:
            self._cache = tempfile.TemporaryDirectory()
            self.cache = pathlib.Path(self._cache.name)

    def cleanup_cache(self):
        if hasattr(self, "_cache"):
            self._cache.cleanup()

    def clean(self, versions: T.List[str]) -> None:
        for download in self.downloads:
            incdir = self.get_dl_include_dir(download)
            if incdir is not None:
                shutil.rmtree(incdir, ignore_errors=True)

            libdir = self.get_dl_lib_dir(download)
            if libdir is not None:
                shutil.rmtree(libdir, ignore_errors=True)

    def get_dl_extract_root(self, download: Download) -> pathlib.Path:
        return pathlib.Path(self.root) / pathlib.PurePosixPath(download.extract_to)

    def get_dl_include_dir(self, download: Download) -> T.Optional[pathlib.Path]:
        if download.incdir is not None:
            return self.get_dl_extract_root(download) / "include"
        return None

    def get_dl_lib_dir(self, download: Download) -> T.Optional[pathlib.Path]:
        if download.libs is not None or download.staticlibs is not None:
            return self.get_dl_extract_root(download) / "lib"
        return None

    def make_lib_map(self, download: Download) -> T.Dict[str, pathlib.Path]:

        to: T.Dict[str, pathlib.Path] = {}

        libdir = self.get_dl_lib_dir(download)
        if libdir is not None:
            assert download.libs is not None or download.staticlibs is not None

            if download.libdir is None:
                raise ValueError(
                    f"{download.url}: no libdir specified as part of download"
                )

            if download.libs is not None:
                for lib in download.libs:
                    name = f"{self.platform.libprefix}{lib}{self.platform.libext}"
                    to[posixpath.join(download.libdir, name)] = libdir / name

                    if self.platform.linkext:
                        name = f"{self.platform.libprefix}{lib}{self.platform.linkext}"
                        to[posixpath.join(download.libdir, name)] = libdir / name

            if download.staticlibs is not None:
                for lib in download.staticlibs:
                    name = f"{self.platform.libprefix}{lib}{self.platform.staticext}"
                    to[posixpath.join(download.libdir, name)] = libdir / name

        return to

    def download(
        self, download: Download, to: T.Dict[str, pathlib.Path]
    ) -> T.List[pathlib.Path]:

        root = self.get_dl_extract_root(download)
        incdir = self.get_dl_include_dir(download)
        libdir = self.get_dl_lib_dir(download)

        if incdir is not None:
            assert download.incdir is not None
            to[download.incdir] = incdir

            if incdir not in self.cleaned_paths:
                self.app.display_info(f"Removing {incdir}")
                shutil.rmtree(incdir, ignore_errors=True)

            self.cleaned_paths.add(incdir)

        if libdir is not None and libdir not in self.cleaned_paths:
            self.app.display_info(f"Removing {libdir}")
            shutil.rmtree(libdir, ignore_errors=True)
            self.cleaned_paths.add(libdir)

        self.app.display_info(f"Downloading {download.url}")
        cached_fname, present = download_file(download.url, self.cache)
        if present:
            self.app.display_info("-> already present in cache")

        if download.files:
            for fname in download.files:
                to[fname] = root / fname

        return extract_zip(cached_fname, to, self.app)

    @functools.cached_property
    def strip_exe(self):
        strip_exe = "strip"
        if getattr(sys, "cross_compiling", False):
            # This is a hack, but the information doesn't seem to be available
            # in other accessible ways
            ar_exe = sysconfig.get_config_var("AR")
            if ar_exe.endswith("-ar"):
                strip_exe = f"{ar_exe[:-3]}-strip"

        return strip_exe

    def strip(self, path: pathlib.Path):
        strip_exe = self.strip_exe
        self.app.display_info(f"+ {strip_exe} {path}")
        subprocess.check_call([strip_exe, str(path)])
