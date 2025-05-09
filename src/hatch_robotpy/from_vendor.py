#!/usr/bin/env python3

"""
Given a vendor json file, spit out hatch-robotpy portions of pyproject.toml and
hatch-nativelib as well.
- requires 'pkgconf', `pefile` and `pyelftools`

"""


import argparse
import dataclasses
import json
import os
import os.path
import pathlib
import re
import shutil
import tempfile
import typing as T

import pkgconf
import tomli_w

from .download import download_file, extract_zip
from .maven import _get_artifact_url
from .platforms import get_platform


validation_re = re.compile(r"[A-Za-z0-9\._-]+")

platmap = {
    "windowsx86-64": get_platform("win-amd64"),
    "linuxx86-64": get_platform("linux-x86_64"),
    "linuxathena": get_platform("linux-roborio"),
    "linuxarm32": get_platform("linux-raspbian"),
    "linuxarm64": get_platform("linux-aarch64"),
    "osxuniversal": get_platform("macos-universal"),
}


@dataclasses.dataclass
class EnableIf:
    include: str
    exclude: str


_enable_if = {
    "windowsx86-64": EnableIf(
        "platform_system=='Windows' and platform_machine=='x86_64'",
        "platform_system!='Windows' and platform_machine!='x86_64'",
    ),
    "linuxx86-64": EnableIf(
        "platform_system=='Linux' and platform_machine=='x86_64'",
        "platform_system!='Linux' and platform_machine!='x86_64'",
    ),
    "linuxathena": EnableIf(
        "platform_machine=='roborio'", "platform_machine!='roborio'"
    ),
    "linuxarm32": EnableIf(
        "platform_system=='Linux' and platform_machine=='armv7l'",
        "platform_system!='Linux' and platform_machine!='armv7l'",
    ),
    "linuxarm64": EnableIf(
        "platform_system=='Linux' and platform_machine=='aarch64'",
        "platform_system!='Linux' and platform_machine!='aarch64'",
    ),
    "osxuniversal": EnableIf(
        "platform_system=='Darwin'",
        "platform_system!='Darwin'",
    ),
}


def run_pkgconf(*args) -> str:
    r = pkgconf.run_pkgconf(*args, capture_output=True, check=True)
    return r.stdout.decode("utf-8").strip()


def get_library_full_names(pkg: str) -> T.Generator[str, None, None]:
    lib = run_pkgconf("-libs-only-l", "--maximum-traverse-depth=1", pkg)[2:]
    if lib:
        for p in platmap.values():
            yield f"{p.libprefix}{lib}{p.libext}"


def is_ok_fname(s) -> bool:
    return validation_re.match(s) and s != ".."  # type: ignore


# from https://gist.github.com/u0pattern/0e9ac6cc6a51ca9551867ee42619dd40
def ldd_elf(fname: pathlib.Path):
    from elftools.elf.elffile import ELFFile

    with open(fname, "rb") as f:
        elffile = ELFFile(f)
        # iter_segments implementation :
        # https://github.com/eliben/pyelftools/blob/master/elftools/elf/elffile.py#L171
        for segment in elffile.iter_segments():
            # PT_DYNAMIC section
            # https://docs.oracle.com/cd/E19683-01/817-3677/chapter6-42444/index.html
            if segment.header.p_type == "PT_DYNAMIC":
                # iter_tags implementation :
                # https://github.com/eliben/pyelftools/blob/master/elftools/elf/dynamic.py#L144-L160
                for t in segment.iter_tags():  # type: ignore
                    # DT_NEEDED section
                    # https://docs.oracle.com/cd/E19683-01/817-3677/6mj8mbtbe/index.html
                    if t.entry.d_tag == "DT_NEEDED":
                        yield t.needed  # type: ignore


def ldd_dll(fname: pathlib.Path):
    import pefile

    pe = pefile.PE(fname)
    for entry in pe.DIRECTORY_ENTRY_IMPORT:  # type: ignore
        yield entry.dll.decode("utf-8")


def ldd_dylib(fname: pathlib.Path):
    import delocate.tools

    for name in delocate.tools.get_install_names(fname):
        yield os.path.basename(name)


def ldd(fname: pathlib.Path):
    if fname.suffix == ".so":
        yield from ldd_elf(fname)
    elif fname.suffix == ".dylib":
        yield from ldd_dylib(fname)
    elif fname.suffix == ".dll":
        yield from ldd_dll(fname)
    else:
        raise ValueError(f"unknown file extension {fname}")


@dataclasses.dataclass
class LibData:
    repo_url: str
    artifact_id: str
    group_id: str
    version: str
    lib_name: str
    pypkg: str
    sim: T.Optional[str]

    # key: name; value: library dependencies
    platforms: T.Dict[str, T.List[str]]

    enable_if: T.Optional[str] = None


def get_deps(pd: T.List[str], internals, externals):
    deps = []
    for dep in pd:
        iname = internals.get(dep)
        ename = externals.get(dep)
        if iname:
            deps.append(iname)
        elif ename:
            deps.append(ename)
    return list(reversed(deps))


def get_enable_if(plats: T.List[str]) -> T.Optional[str]:
    if len(plats) == len(platmap):
        return None
    elif len(plats) == 1:
        return _enable_if[plats[0]].include

    to_exclude = list(set(platmap.keys()) - set(plats))
    if len(to_exclude) == 1:
        return _enable_if[to_exclude[0]].exclude
    elif len(to_exclude) == 2:
        e0 = _enable_if[to_exclude[0]].exclude
        e1 = _enable_if[to_exclude[1]].exclude

        return f"({e0}) and ({e1})"

    print(plats)
    assert False  # TODO


def print_download(d):
    t = {
        "tool": {
            "hatch": {
                "build": {
                    "hooks": {
                        "robotpy": {"maven_lib_download": [d]},
                    }
                }
            }
        }
    }
    print(tomli_w.dumps(t))


def print_pcfile(d):
    t = {
        "tool": {
            "hatch": {
                "build": {
                    "hooks": {
                        "nativelib": {"pcfile": [d]},
                    }
                }
            }
        }
    }
    print(tomli_w.dumps(t))


class App:
    def display_info(self, msg):
        print(msg)


def main():

    # Use a cache
    if "HATCH_ROBOTPY_CACHE" in os.environ:
        cache = pathlib.Path(os.environ["HATCH_ROBOTPY_CACHE"])
        cache.mkdir(parents=True, exist_ok=True)
    else:
        cache_ = tempfile.TemporaryDirectory()
        cache = pathlib.Path(cache_.name)

    app = App()

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("vendor_json")
    parser.add_argument("package")
    parser.add_argument(
        "-s", "--use-src", help="Python packages live in src", action="store_true"
    )
    parser.add_argument(
        "--pcfile",
        help="Emit hatch-nativelib .pc configuration",
        action="store_true",
    )
    args = parser.parse_args()

    try:
        with open(args.vendor_json) as fp:
            data = json.load(fp)
    except:
        from urllib.request import urlopen

        with urlopen(args.vendor_json) as fp:
            data = json.load(fp)

    maven_url = data["mavenUrls"][0].rstrip("/")
    # .. version doesn't matter

    our_libs: T.Dict[str, LibData] = {}
    internals = {}
    internals_set = set()

    for dep in data["cppDependencies"]:
        # construct the URL

        libName = dep["libName"]
        artifact_id = dep["artifactId"].lower().replace("-", "_")
        pkgname = f"{args.package}_{artifact_id}"
        pypkg = f"{args.package}._{artifact_id}"

        assert pkgname not in our_libs, pkgname

        ld = LibData(
            repo_url=maven_url,
            artifact_id=dep["artifactId"],
            group_id=dep["groupId"],
            version=dep["version"],
            lib_name=libName,
            sim=dep.get("simMode"),
            pypkg=pypkg,
            platforms={},
        )

        our_libs[pkgname] = ld
        internals_set.add(pkgname)

        # validate the pieces
        for piece in (ld.artifact_id, ld.group_id, ld.version):
            if not is_ok_fname(piece):
                raise ValueError(piece)

        unzip_to = cache / f"{ld.group_id}.{ld.artifact_id}"
        if unzip_to.exists():
            shutil.rmtree(str(unzip_to))

        for platName in dep["binaryPlatforms"]:
            if platName not in ["linuxx86-64", "linuxathena"]:
                ld.platforms[platName] = []
                continue

            platform = platmap[platName]

            # download it, unzip it to the cache
            arch = platName[5:]
            url = _get_artifact_url(
                ld,  # type: ignore
                platName,
            )

            dlpath, _ = download_file(url, cache)
            extract_zip(
                dlpath,
                {"": unzip_to},
                app,  # type: ignore
            )

            # find the library, add its dependencies
            lib_fname = (
                unzip_to
                / platform.os
                / platform.arch
                / "shared"
                / f"{platform.libprefix}{libName}{platform.libext}"
            )

            internals[f"{platform.libprefix}{libName}{platform.libext}"] = pkgname

            ld.platforms[platName] = list(ldd(lib_fname))

        ld.enable_if = get_enable_if(list(ld.platforms.keys()))

    # populate all pkgconf data here
    externals = {}
    for pkgname in run_pkgconf("--list-package-names").split("\n"):
        if not pkgname:
            continue

        for lib in get_library_full_names(pkgname):
            externals[lib] = pkgname

    # Ok, now we have enough data to populate depends, and write it all out
    print("#")
    print("# Autogenerated TOML via `python3 -m hatch_robotpy.from_vendor`")
    print("#")
    print()

    if args.use_src:
        extract_root = pathlib.Path("src")
    else:
        extract_root = pathlib.Path()

    for name, data in our_libs.items():

        if data.sim == "hwsim":
            # only keep roborio
            keys = list(data.platforms.keys())
            for k in keys:
                if k != "linuxathena":
                    del data.platforms[k]

            if not data.platforms:
                continue

        #
        # Download first
        #

        extract_to = extract_root / args.package

        download = {
            "artifact_id": data.artifact_id,
            "group_id": data.group_id,
            "repo_url": data.repo_url,
            "version": data.version,
            "libs": [data.lib_name],
            "extract_to": extract_to.as_posix(),
        }

        if data.enable_if:
            download["enable_if"] = data.enable_if

        print_download(download)

        #
        # pcfile second
        #

        # assume that requirements are the same or empty
        athena_plat = data.platforms.get("linuxathena")
        sim_plat = data.platforms.get("linuxx86-64")

        if athena_plat is None:
            if sim_plat is None:
                requires = None
            else:
                requires = get_deps(sim_plat, internals, externals)
        else:
            if sim_plat is None:
                requires = get_deps(athena_plat, internals, externals)
            else:
                requires = get_deps(athena_plat, internals, externals)
                sim_requires = get_deps(sim_plat, internals, externals)

                if set(requires) != set(sim_requires):
                    print("# WARNING: different requirements for sim/roborio")
                    print("# sim:", sim_requires)
                    print("# rio:", requires)
                    print()

                    requires = list(set(requires) | set(sim_requires))

        pcfile = {
            "pcfile": (extract_to / f"{name}.pc").as_posix(),
            "name": name,
            "version": data.version,
            "includedir": (extract_to / "include").as_posix(),
            "libdir": (extract_to / "lib").as_posix(),
            "shared_libraries": [data.lib_name],
        }

        if requires:
            pcfile["requires"] = requires

        if data.enable_if:
            pcfile["enable_if"] = data.enable_if

        if args.pcfile:
            print_pcfile(pcfile)

    print("# End autogenerated TOML")


if __name__ == "__main__":
    main()
