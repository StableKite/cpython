"""Qualify frozen Greenlet on a separate source-built musl ARM runtime."""

from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import tarfile
import urllib.request


CPYTHON_URL = (
    "https://codeload.github.com/python/cpython/tar.gz/refs/tags/v3.15.2"
)
CPYTHON_SHA = (
    "4e35a9145e728fa51c4950d3fe490c419dc1587c7ea21757a50cb5f393762893"
)
FIXED_STATE_SHA = (
    "8ece25ec0f0667578078700b9cdf98d44a9b3d169539fbb46baf25df754bdec1"
)
GREENLET_HEAD = "2e09ddbe7e708576209b74a1d8b1e64f1022120b"
GREENLET_URL = (
    "https://codeload.github.com/StableKite/greenlet/tar.gz/"
    + GREENLET_HEAD
)
GREENLET_SHA = (
    "67615149410b86b7c85a764e7f7fb4a7dfa7958113bb6de6acb5bb634db5bec5"
)
TEST_URL = (
    "https://raw.githubusercontent.com/StableKite/cpython/"
    "920a86d1f31dd099d7b7aac434863aaaca2e8f21/"
    "Lib/test/test_free_threading/test_os.py"
)
TEST_SHA = (
    "1f69a641968f01f90ca525ea27f852cc496da057b87e0f8319b19897b635b47a"
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    assert platform.machine() == "aarch64", platform.machine()
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    work = Path("/own-arm-source-work")
    work.mkdir()
    out = Path("/output")
    out.mkdir(exist_ok=True)
    rows = []
    env = dict(os.environ)
    for name in ("PYTHON_GIL", "PYTHONPATH", "PYTHONHOME", "GITHUB_SHA"):
        env.pop(name, None)
    env.update(PYTHON_JIT="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONFAULTHANDLER="1")

    def run(name, args, *, timeout=180, cwd=work, selected_env=None,
            required=True):
        args = list(map(str, args))
        record = {"name": name, "argv": args, "cwd": str(cwd),
                  "timeout_seconds": timeout}
        rows.append(record)
        with (out / (name + ".stdout.log")).open("xb") as stdout, \
                (out / (name + ".stderr.log")).open("xb") as stderr:
            try:
                result = subprocess.run(
                    args, cwd=cwd, env=selected_env or env,
                    stdout=stdout, stderr=stderr, timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                record["exit_code"] = "TIMEOUT"
                raise
            else:
                record["exit_code"] = result.returncode
            finally:
                (out / "STAGES.json").write_text(
                    json.dumps(rows, indent=2) + "\n"
                )
        print(name, result.returncode, flush=True)
        if required:
            result.check_returncode()
        return result.returncode

    def download(url, filename, expected):
        target = work / filename
        with urllib.request.urlopen(url, timeout=90) as response, \
                target.open("xb") as stream:
            shutil.copyfileobj(response, stream)
        assert digest(target) == expected, filename
        return target

    run("native-container-inventory", ["uname", "-a"])
    run("native-compiler", ["gcc", "--version"])
    run("extension-compiler", ["clang++", "--version"])
    archive = download(CPYTHON_URL, "cpython.tar.gz", CPYTHON_SHA)
    greenlet_archive = download(GREENLET_URL, "greenlet.tar.gz", GREENLET_SHA)
    test = download(TEST_URL, "test_os.py", TEST_SHA)
    shutil.copyfile(test, out / "public_test_os.py")
    with tarfile.open(archive) as bundle:
        bundle.extractall(work, filter="data")
    with tarfile.open(greenlet_archive) as bundle:
        bundle.extractall(work, filter="data")
    source = work / "cpython-3.15.2"
    greenlet_source = work / ("greenlet-" + GREENLET_HEAD)
    state = source / "Python/pystate.c"
    original = state.read_text()
    version_header = digest(source / "Include/patchlevel.h")
    env["LD_LIBRARY_PATH"] = str(source)
    inputs = {
        "status": "PRIVATE_NATIVE_ARM_SOURCE_RUNTIME_NOT_STOCK_SDK",
        "controller_sha": os.environ["STABLEKITE_CONTROLLER_SHA"],
        "CPython_source": {"tag": "v3.15.2", "url": CPYTHON_URL,
                           "archive_sha256": CPYTHON_SHA},
        "Greenlet_source": {"head": GREENLET_HEAD, "url": GREENLET_URL,
                            "archive_sha256": GREENLET_SHA},
        "public_test": {"url": TEST_URL, "sha256": TEST_SHA},
        "architecture": platform.machine(),
        "stock_SDK_or_Root_modified": False,
        "GITHUB_SHA_overridden_for_package_provenance": False,
    }
    (out / "INPUTS.json").write_text(json.dumps(inputs, indent=2) + "\n")
    run("configure-original-release-FT", [
        "./configure", "--disable-gil", "--enable-shared",
        "--with-ensurepip=no", "--without-static-libpython",
    ], cwd=source, timeout=300)
    run("compile-original-release-FT", ["make", "-j2"],
        cwd=source, timeout=1800)
    python = source / "python"
    identity = """from pathlib import Path
from hashlib import sha256
import json,os,platform,sys,sysconfig
assert sys.version_info[:3]==(3,15,2) and platform.machine()=='aarch64'
assert sysconfig.get_config_var('Py_GIL_DISABLED')==1
assert sysconfig.get_config_var('Py_DEBUG')==0
assert 'PYTHON_GIL' not in os.environ
assert sys.flags.gil is None and not sys._is_gil_enabled()
assert not sys._jit.is_available() or sys._jit.is_enabled()
mapped={Path(line.split()[-1]).resolve() for line in Path('/proc/self/maps').read_text().splitlines() if 'libpython3.15t.so' in line}
assert len(mapped)==1
library=mapped.pop()
assert library==Path(sys.argv[1]).resolve()
assert 'greenlet' not in sys.modules
print(json.dumps({'version':sys.version,'architecture':platform.machine(),'libpython':str(library),'libpython_sha256':sha256(library.read_bytes()).hexdigest(),'GIL':False,'gil_flag':None,'Py_DEBUG':0}))
"""
    wrapper = """import runpy,sys
assert sys.flags.gil is None and not sys._is_gil_enabled()
sys.argv=[sys.argv[1],'TestOs.test_directory_io_during_subinterpreter_gc','-v']
try:
 runpy.run_path(sys.argv[0],run_name='__main__')
except SystemExit as error:
 code=error.code
else:
 code=0
assert sys.flags.gil is None and not sys._is_gil_enabled()
raise SystemExit(code)
"""
    phases = []
    for phase in ("before", "after"):
        if phase == "after":
            start = original.index(
                "void\n_PyThreadState_Attach(PyThreadState *tstate)"
            )
            end = original.index("\nstatic void\ndetach_thread(", start)
            segment = original[start:end]
            assert segment.count("#if defined(Py_DEBUG)") == 2
            fixed = segment.replace(
                "#if defined(Py_DEBUG)\n    // This is called",
                "    // This is called", 1,
            ).replace("    int err = errno;\n#endif", "    int err = errno;", 1)
            fixed = fixed.replace(
                "#if defined(Py_DEBUG)\n    errno = err;\n#endif",
                "    errno = err;", 1,
            )
            fixed = original[:start] + fixed + original[end:]
            state.write_text(fixed)
            assert digest(state) == FIXED_STATE_SHA
            patch = "".join(difflib.unified_diff(
                original.splitlines(True), fixed.splitlines(True),
                fromfile="a/Python/pystate.c", tofile="b/Python/pystate.c",
            ))
            (out / "CPYTHON_ERRNO.patch").write_text(patch)
            run("compile-source-errno-fix", ["make", "-j2"],
                cwd=source, timeout=300)
        assert digest(test) == TEST_SHA
        assert digest(source / "Include/patchlevel.h") == version_header
        phase_out = out / ("native-" + phase)
        phase_out.mkdir()
        for path in (python, source / "libpython3.15t.so.1.0", state):
            shutil.copyfile(path, phase_out / path.name)
        run(phase + "-actual-release-runtime", [python, "-I", "-B",
            "-c", identity, source / "libpython3.15t.so.1.0"])
        code = run(phase + "-unchanged-public-regression", [
            python, "-I", "-B", "-c", wrapper, test,
        ], timeout=900, required=False)
        log = (out / (phase + "-unchanged-public-regression.stderr.log"))
        raw = log.read_bytes()
        assert b"Ran 1 test" in raw and b"skipped" not in raw
        if phase == "before":
            assert code == 0 or code == 1 and (
                b"BlockingIOError(11," in raw or b"[Errno 11]" in raw
            )
        else:
            assert code == 0
        phases.append({"phase": phase, "exit_code": code,
                       "test_sha256": digest(test),
                       "pystate_sha256": digest(state),
                       "libpython_sha256": digest(
                           phase_out / "libpython3.15t.so.1.0")})
        (out / "PUBLIC_PAIR.json").write_text(
            json.dumps(phases, indent=2) + "\n"
        )
    assert phases[0]["libpython_sha256"] != phases[1]["libpython_sha256"]
    shutil.copyfile(source / "LICENSE", out / "CPython-LICENSE.txt")
    consumer = work / "greenlet-env"
    run("fresh-private-native-FT-env", [python, "-I", "-B", "-m",
        "venv", consumer])
    consumer_python = consumer / "bin/python"
    extension_env = dict(env, CC="clang", CXX="clang++",
                         GREENLET_STATIC_RUNTIME="1")
    run("ordinary-source-build-tools", [consumer_python, "-m", "pip",
        "install", "--upgrade", "build", "setuptools", "pyright==1.1.414",
        "auditwheel==6.8.2"], selected_env=extension_env, timeout=300)
    acceptance = work / "greenlet-acceptance"
    tools = greenlet_source / "tools/acceptance"
    try:
        run("full-unchanged-Greenlet-acceptance", [consumer_python,
            "-I", "-B", tools / "run.py", greenlet_source, acceptance],
            selected_env=extension_env, timeout=900)
    finally:
        if acceptance.exists():
            run("preserve-original-Greenlet-carrier", [
                "/usr/local/bin/python", "-I", "-B",
                tools / "prepare_evidence.py", acceptance, out / "greenlet",
            ])
    receipt = json.loads((acceptance / "RECEIPT.json").read_text())
    assert receipt["status"] == "PASSED_ALL_DECLARED_COMPONENT_CHECKS"
    result = {
        "status": "PASS_FULL_NATIVE_MUSL_ARM_WITH_PRIVATE_SOURCE_ERRNO_FIX",
        "inputs": inputs, "public_regression_phases": phases,
        "before_failed_with_EAGAIN": phases[0]["exit_code"] == 1,
        "Greenlet_original_acceptance_unchanged": True,
        "Greenlet_original_177_and_fresh_consumer_177": True,
        "original_workload_timeouts_or_assertions_changed": False,
        "stock_musl_runtime_gate": "OPEN",
        "canonical_default_or_release_changed": False,
        "Root_or_standard_SDK_modified": False,
        "created_utc": datetime.now(timezone.utc).isoformat(),
    }
    (out / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(result["status"], flush=True)


if __name__ == "__main__":
    main()
