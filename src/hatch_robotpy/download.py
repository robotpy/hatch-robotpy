import contextlib
import os
import pathlib
import posixpath
import shutil
import time
import typing as T
import urllib.request
import zipfile

from ._version import __version__

if T.TYPE_CHECKING:
    from hatchling.bridge.app import Application


USER_AGENT = f"hatch-robotpy/{__version__}"


def download_file(url: str, cache: pathlib.Path) -> T.Tuple[pathlib.Path, bool]:
    """
    :returns: path of file, and whether it was already cached
    """

    fname = posixpath.basename(url)
    cached_fname = cache / fname
    present = cached_fname.exists()

    if not present:
        tmp_cached_fname = cached_fname.with_suffix(".tmp")
        with open(tmp_cached_fname, "wb") as fp:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with contextlib.closing(urllib.request.urlopen(request)) as ufp:
                while True:
                    block = ufp.read(1024 * 8)
                    if not block:
                        break
                    fp.write(block)

        os.replace(tmp_cached_fname, cached_fname)

    return cached_fname, present


def _set_mtime(info: zipfile.ZipInfo, dst: pathlib.Path):
    ftime = time.mktime(info.date_time + (0, 0, -1))
    os.utime(dst, (ftime, ftime))


def extract_zip(
    fname: pathlib.Path, to: T.Dict[str, pathlib.Path], app: "Application"
) -> T.List[pathlib.Path]:
    """
    Utility method intended to be useful for downloading/extracting
    third party source zipfiles

    :param cache: directory to cache downloads to
    :param to: is a dict of {src: dst}
    """
    extracted: T.List[pathlib.Path] = []

    app.display_info(f"Extracting {fname.name}")

    with zipfile.ZipFile(fname) as z:
        for src, dst in to.items():
            info = None
            if src != "":
                # if is directory, copy whole thing recursively
                try:
                    info = z.getinfo(src)
                except KeyError as e:
                    osrc = src
                    src = src + "/"
                    try:
                        info = z.getinfo(src)
                    except KeyError:
                        info = None
                    if info is None:
                        msg = f"error extracting {osrc} from {fname}"
                        raise ValueError(msg) from e

                assert info is not None
                src = info.filename

            if src == "" or (info is not None and info.is_dir()):
                ilen = len(src)
                for minfo in z.infolist():
                    if minfo.is_dir():
                        continue
                    srcname = posixpath.normpath(minfo.filename)
                    if srcname.startswith(src):
                        dstname = dst / srcname[ilen:]
                        dstname.parent.mkdir(parents=True, exist_ok=True)

                        with z.open(minfo.filename, "r") as zfp, open(
                            dstname, "wb"
                        ) as fp:
                            shutil.copyfileobj(zfp, fp)

                        _set_mtime(minfo, dstname)

                        app.display_info(f"- {srcname} => {dstname}")
                        extracted.append(dstname)
            else:
                # otherwise write a single file
                dst.parent.mkdir(parents=True, exist_ok=True)
                with z.open(src, "r") as zfp, open(dst, "wb") as fp:
                    shutil.copyfileobj(zfp, fp)

                info = z.getinfo(src)
                _set_mtime(info, dst)

                app.display_info(f"- {src} => {dst}")
                extracted.append(dst)

        return extracted
