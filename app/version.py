"""Derive a release version from Git, or use the version frozen in a package."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent.parent
TZ = timezone(timedelta(hours=8))


def version_from_timestamps(timestamps):
    dates = [datetime.fromtimestamp(int(value), TZ) for value in timestamps]
    day = dates[0]
    count = sum(value.date() == day.date() for value in dates)
    return f'{day:%y.%m%d}.{count}'


def git(*args):
    return subprocess.check_output(
        ['git', '-C', str(ROOT), *args], stderr=subprocess.DEVNULL,
        text=True, timeout=10,
    ).strip()


@lru_cache(maxsize=16)
def version_at_commit(commit):
    # First-parent history gives merges one release number on this branch.
    return version_from_timestamps(git('log', '--first-parent', '--format=%ct', commit).splitlines())


def get_version():
    if (ROOT / '.git').exists():
        # Read HEAD each time so a running development server sees new commits.
        # An incomplete history must not silently produce a wrong sequence.
        if git('rev-parse', '--is-shallow-repository') == 'true':
            raise RuntimeError('Version generation requires full Git history; run git fetch --unshallow')
        return version_at_commit(git('rev-parse', 'HEAD'))
    return (ROOT / 'app/version.txt').read_text(encoding='utf-8').strip()


if __name__ == '__main__':
    version = get_version()
    (ROOT / 'app/version.txt').write_text(version + '\n', encoding='utf-8')
    print(version)
