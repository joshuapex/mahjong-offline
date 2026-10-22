"""Freeze the local Git version, then build without sending Git to Docker.

Usage: python build.py [-f compose.nas.yaml] [other compose options]
"""
import subprocess
import sys

from app.version import ROOT, get_version


def main():
    version=get_version()
    (ROOT/'app/version.txt').write_text(version+'\n',encoding='utf-8')
    print('Building version '+version,flush=True)
    subprocess.run(['docker','compose',*sys.argv[1:],'build'],cwd=ROOT,check=True)


if __name__ == '__main__':
    main()
