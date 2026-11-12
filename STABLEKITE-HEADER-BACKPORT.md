# CPython 3.15.2 public-header backports

This release branch starts at the official 3.15.2 tag and carries two
already merged upstream 3.15 corrections, preserving their authors:

- beff3c022fe787f6879c7e38a6b8a64243184ab3 / PR161874: guard uses of
  `_Py_STDC_VERSION` in pyport.h and cpython/pyatomic.h and strengthen the
  public C/C++ extension tests with `-Werror=undef`.
- 9b6a1a1abf3febbe7ce24dd6fc46c846e544a480 / PR160825: restore the defined
  `_Py_STDC_VERSION` condition for the C11-C17 static_assert fallback.

The second is necessary for C++ validation of this release: applying only
the first still fails in pymacro.h. No compiler diagnostics are disabled.
These are public-header/preprocessor corrections; runtime C sources and
the release version are unchanged. No duplicate upstream PR is needed.

Validated 12 November 2026 on isolated Ubuntu Stonking amd64/GCC15.3:

- Original installed 3.15.2t headers fail on `_Py_STDC_VERSION` with
  `-Werror=undef` in C11, C17, C23, C++11, C++17 and C++20. Corrected headers
  pass all six translation-unit checks with the same compiler flags.
- Full source at 1af0b0b9395e54fdf054ae6ead360c38a3d66743 was configured,
  compiled and installed afresh under a separate prefix, with `--disable-gil`.
  `python -m test test_cext -u cpu -v`: 16 run, 15 passed, one existing skip.
- On that freshly built interpreter, official Pycairo1.29.2 builds a real
  cp315t wheel with `CFLAGS='-Werror -Wundef -coverage'`. Its upstream tests
  pass: 285 passed, two existing skips. GIL remains disabled after the suite
  without `PYTHON_GIL` override.

The PyGObject fork uses this rolling release branch to prepare the three
affected public headers in the CPython3.15.2 CI toolchain. Each checkout's
actual SHA is printed for provenance; no installation commit is pinned.
This branch does not establish full CPython, ARM64 or device acceptance.
