"""Start cloud diagnostics before importing a production service."""
import runpy
import sys

import research_diagnostics as diagnostics


SPOOLS = {
    "web": "/app/data/research-diagnostics",
    "intraday-observer": "/app/logs/observer-diagnostics",
    "execution-agent": "/app/logs/execution-diagnostics",
    "shadow-worker": "/app/shadow/diagnostics",
    "research-reporting": "/tmp/research-reporting-diagnostics",
    "calibration-worker": "/app/calibration/diagnostics",
}
MODULES = {
    "intraday-observer": "intraday_observer",
    "execution-agent": "agent_entrypoint",
    "shadow-worker": "shadow_worker",
    "research-reporting": "research.intraday_reporting",
    "calibration-worker": "research.calibration_worker",
}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in SPOOLS:
        raise SystemExit("Select web, intraday-observer, execution-agent, shadow-worker, calibration-worker or research-reporting.")
    service, *forwarded = args
    diagnostics.start(service, SPOOLS[service])
    previous = sys.argv
    try:
        sys.argv = [MODULES.get(service, "uvicorn"), *forwarded]
        if service == "web":
            import uvicorn
            uvicorn.run("main:app", host="0.0.0.0", port=8000)
        else:
            runpy.run_module(MODULES[service], run_name="__main__")
        diagnostics.emit(service, "service_exit", level="INFO", context={"exit_code": 0})
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        diagnostics.emit(service, "service_exit", level="INFO" if code == 0 else "CRITICAL",
                         context={"exit_code": code})
        raise
    except BaseException as exc:
        diagnostics.emit(service, "service_crash", error=exc, level="CRITICAL")
        raise
    finally:
        sys.argv = previous
        diagnostics.close()


if __name__ == "__main__":
    main()
