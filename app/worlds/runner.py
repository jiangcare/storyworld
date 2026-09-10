"""Child process for trusted installed calculators. No model supplied code or paths.

This is a resource-limited runner, not a sandbox for untrusted Python packages.
Pack installation is a developer/operator action, never a player tool.
"""
import json
from pathlib import Path
import runpy
import sys


def main():
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (2, 2))
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    request = json.loads(sys.stdin.read(262145))
    sys.path.insert(0, str(Path(sys.argv[1]).parent))
    module = runpy.run_path(sys.argv[1])
    try:
        output = module['calculate'](request)
    except (ValueError, KeyError, TypeError, IndexError) as exc:
        # Authored scripts raise short in-world explanations, never raw input.
        output = {'ok': False, 'error': str(exc) if isinstance(exc, ValueError) else '条件尚不充分，这次没有执行。'}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == '__main__':
    main()
