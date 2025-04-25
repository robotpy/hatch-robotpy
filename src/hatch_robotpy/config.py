import dataclasses
import re
import typing as T

_arch_re = re.compile(r"\$\{ARCH\}")
_os_re = re.compile(r"\$\{OS\}")


@dataclasses.dataclass
class MavenLibDownload:
    """
    Used to download artifacts from a maven repository. This can download
    headers, shared libraries, and sources.

    .. code-block:: toml

       [[tool.hatch.build.hooks.robotpy.maven_lib_download]]
       artifact_id = "mything"
       group_id = "com.example.thing"
       repo_url = "http://example.com/maven"
       version = "1.2.3"

    .. note:: For FIRST Robotics libraries, the information required can
              be found in the vendor JSON file
    """

    #: Relative directory to extract to, specified as posix path
    #:
    #: * header artifacts will be extracted to ${extract_to}/include
    #: * library artifacts will be extracted to ${extract_to}/lib
    #: * The lib and include output directory will be deleted when content is updated
    extract_to: str

    #: Maven artifact ID
    artifact_id: str

    #: Maven group ID
    group_id: str

    #: Maven repository URL
    repo_url: str

    #: Version of artifact to download
    version: str

    #: Configure the sources classifier when sources are downloaded
    sources_classifier: str = "sources"

    #: When set, download sources instead of downloading libraries.
    use_sources: bool = False

    #: When set, attempt to download header artifact
    use_headers: bool = True

    # common with Download

    #: Names of contained shared libraries. If None and staticlibs is None, set to artifact_id.
    libs: T.Optional[T.List[str]] = None

    #: Names of contained static libraries
    staticlibs: T.Optional[T.List[str]] = None

    #: If :attr:`use_sources` is set, this is the list of sources to extract from the zip
    sources: T.Optional[T.List[str]] = None

    #: If :attr:`use_sources` is set, apply the following patches to the sources. Patches
    #: must be in unified diff format.
    # patches: T.Optional[T.List[PatchInfo]] = None

    #: Patches to downloaded header files. Patches must be in unified diff format.
    # header_patches: T.Optional[T.List[PatchInfo]] = None

    #: On Linux, strips debug symbols from the library. If not specified,
    #: defaults to true when building a wheel and false when in editable mode
    strip: T.Optional[bool] = None

    #: This is a PEP 508 environment marker specification.
    #:
    #: This download will be only enabled if the environment marker matches the
    #: current build environment.
    enable_if: T.Optional[str] = None


@dataclasses.dataclass
class Download:
    """
    Download sources/libs/includes from a single file

    .. code-block:: toml

       [[tool.hatch.build.hooks.robotpy.download]]
       url = "https://my/url/something.zip"
       incdir = "include"
       libs = ["mylib"]

    """

    #: Relative directory to extract to, specified as posix path
    extract_to: str

    #: URL of zipfile to download
    #:
    #: ${ARCH} and ${OS} are replaced with the architecture/os name
    url: str

    #: Directory within downloaded file that contains include files.
    #:
    #: * ${ARCH} and ${OS} are replaced with the architecture/os name
    #: * Output directory is ${extract_to}/include
    #: * Output directory will be deleted when content is updated
    incdir: T.Optional[str] = None

    #: Directory within downloaded file that contains library files
    #:
    #: * ${ARCH} and ${OS} are replaced with the architecture/os name
    #: * Output directory is ${extract_to}/lib
    #: * Output directory will be deleted when content is updated
    libdir: T.Optional[str] = None

    #: List of files to extract from the zipfile
    files: T.Optional[T.List[str]] = None

    # Common with MavenLibDownload

    #: If specified, names of contained shared libraries
    libs: T.Optional[T.List[str]] = None

    #: If specified, names of contained static libraries
    staticlibs: T.Optional[T.List[str]] = None

    #: On Linux, strips debug symbols from the library. If not specified,
    #: defaults to true when building a wheel and false when in editable mode
    strip: T.Optional[bool] = None

    #: If :attr:`sources` is set, apply the following patches to the sources. Patches
    #: must be in unified diff format.
    # patches: T.Optional[T.List[PatchInfo]] = None

    #: Patches to downloaded header files in incdir. Patches must be in unified
    #: diff format.
    # header_patches: T.Optional[T.List[PatchInfo]] = None

    #: This is a PEP 508 environment marker specification.
    #:
    #: This download will be only enabled if the environment marker matches the
    #: current build environment.
    enable_if: T.Optional[str] = None

    def _update_with_platform(self, platform):
        for n in ("url", "incdir", "libdir"):
            v = getattr(self, n, None)
            if v is not None:
                v = _os_re.sub(platform.os, _arch_re.sub(platform.arch, v))
                setattr(self, n, v)


@dataclasses.dataclass
class HookConfig:
    maven_lib_download: T.List[MavenLibDownload] = dataclasses.field(
        default_factory=list
    )

    download: T.List[Download] = dataclasses.field(default_factory=list)
