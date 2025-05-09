hatch-robotpy
=============

Hatchling plugins intended to be used for building RobotPy projects. Contains
the following functionality:

* Download a zip file from a specified URL
* Download a zip file from a maven repository using maven coordinates

See [config](src/hatch_robotpy/config.py) for `pyproject.toml` configuration.

The downloaded files can be cached and reused in future builds. Set the
`HATCH_ROBOTPY_CACHE` environment variable to a cache directory.

Tools
-----

* `hatch_robotpy.from_vendor` can take a WPILib vendor JSON file and output
  the needed portions of `pyproject.toml`
