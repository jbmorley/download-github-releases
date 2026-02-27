#!/usr/bin/env python3

# Copyright (c) 2021-2026 Jason Morley
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
import contextlib
import gzip
import hashlib
import http
import json
import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.parse

import requests


def download_file(url, path):
    headers = {
        "Accept": "application/octet-stream",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if "GITHUB_TOKEN" in os.environ:
        headers["Authorization"] = f"Bearer {os.environ["GITHUB_TOKEN"]}"

    logging.info("Downloading '%s'...", url)

    filename = os.path.basename(path)
    with tempfile.TemporaryDirectory() as directory:
        temporary_path = os.path.join(directory, filename)
        response = get_with_backoff(url, headers=headers, stream=True)
        response.raise_for_status()
        with open(temporary_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=1024):
                if chunk:
                    f.write(chunk)
        shutil.move(temporary_path, path)
        return path


@contextlib.contextmanager
def chdir(path):
    try:
        pwd = os.getcwd()
        os.chdir(path)
        yield
    finally:
        os.chdir(pwd)


def makedirs(path):
    if not os.path.isdir(path):
        os.makedirs(path)


def shasum(path):
        sha256 = hashlib.sha256()
        if os.path.isdir(path):
            for f in sorted(listdir(path, include_hidden=False)):
                sha256.update(shasum(os.path.join(path, f)).encode('utf-8'))
        else:
            with open(path, 'rb') as f:
                while True:
                    data = f.read(65536)
                    if not data:
                        break
                    sha256.update(data)
        return sha256.hexdigest()


def spinning_cursor():
    while True:
        for cursor in ["zzzz", "Zzzz", "zZzz", "zzZz", "zzzZ"]:
            yield cursor


class Sleeper:

    def __init__(self):
        self.polling_duration = 0.2
        self.duration = 0
        self.did_sleep = False
        self.spinner = spinning_cursor()
        self.is_interactive = sys.stdout.isatty()

    def sleep(self, duration):
        self.did_sleep = True
        self.duration = duration
        while self.duration > 0:
            if self.is_interactive:
                print("\r", end="")
                print(next(self.spinner), end="")
            time.sleep(self.polling_duration)
            self.duration = self.duration - self.polling_duration

    def finalize(self):
        if self.did_sleep and self.is_interactive:
            print("\r", end="")


def get_with_backoff(url, *args, **kwargs):
    sleeper = Sleeper()
    duration = 8
    while True:
        logging.debug(f"GET {url}")
        try:
            response = requests.get(url, *args, **kwargs)
            if not response.status_code in [403, 429, 502, 504]:
                break
        except http.client.RemoteDisconnected:
            pass
        did_wait = True
        sleeper.sleep(duration)
        duration = min(duration * 2, 60)
    response.raise_for_status()
    sleeper.finalize()
    return response


def get(url, *args, **kwargs):
    if "headers" not in kwargs:
        kwargs["headers"] = {}
    kwargs["headers"]["Accept"] = "application/vnd.github+json"
    kwargs["headers"]["X-GitHub-Api-Version"] = "2022-11-28"
    if "GITHUB_TOKEN" in os.environ:
        kwargs["headers"]["Authorization"] = f"Bearer {os.environ["GITHUB_TOKEN"]}"
    return get_with_backoff(url, *args, **kwargs)


def gh_release_assets(url, filter=lambda x: True):
    assets = requests.get(url).json()["assets"]
    urls = [asset["browser_download_url"] for asset in assets if filter(asset["name"])]
    return urls


def gh_releases(repository):
    return get_paginated(f"https://api.github.com/repos/{repository}/releases", params={
        "per_page": 100,
    })


def get_paginated(url, *args, **kwargs):
    response = get(url, *args, **kwargs)
    while True:
        for item in response.json():
            yield item
        if not "next" in response.links:
            return
        response = get(response.links["next"]["url"], *args, **kwargs)


def configure_logging():
    verbose = '--verbose' in sys.argv[1:] or '-v' in sys.argv[1:]

    logger = logging.getLogger()
    logger.setLevel(level=logging.DEBUG if verbose else logging.INFO)

    formatter = logging.Formatter("[%(levelname)s] %(message)s")

    handler_out = logging.StreamHandler(sys.stdout)
    handler_out.setLevel(logging.DEBUG)
    handler_out.addFilter(lambda r: r.levelno <= logging.INFO)
    handler_out.setFormatter(formatter)
    logger.addHandler(handler_out)

    handler_err = logging.StreamHandler(sys.stderr)
    handler_err.setLevel(logging.WARNING)
    handler_err.setFormatter(formatter)
    logger.addHandler(handler_err)


def main():
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Download (and keep in sync) the releases from a set of GitHub projects.",
        epilog="Set the GITHUB_TOKEN environment variable to use token-based authentication. This is required for accessing private repositories and can help with API rate limits."
    )
    parser.add_argument('--verbose', '-v', action='store_true', default=False, help="show verbose output")
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument("repository", nargs="+", help="GitHub repository to fetch releases from")
    options = parser.parse_args()

    output_directory = os.path.abspath(options.output)

    for repository in options.repository:
        repository_directory = os.path.join(output_directory, repository)
        os.makedirs(repository_directory, exist_ok=True)

        logging.info("Updating '%s'...", repository)
        for release in gh_releases(repository):
            logging.info("Checking '%s'...", release["tag_name"])
            release_directory = os.path.join(repository_directory, release["tag_name"])
            os.makedirs(release_directory, exist_ok=True)

            with open(os.path.join(release_directory, "_release.json"), "w") as fh:
                json.dump(release, fh, indent=4)
                fh.write("\n")

            with open(os.path.join(release_directory, "_description.md"), "w") as fh:
                fh.write(f"# {release["name"]}\n\n")
                fh.write(release["body"])
                fh.write("\n")

            for asset in release["assets"]:
                asset_url = asset["url"]
                asset_path = os.path.join(release_directory, asset["name"])

                # Check to see if a local copy exists, using the digest if available.
                if "digest" in asset and asset["digest"] is not None:
                    digest_kind, digest_value = asset["digest"].split(":")
                    if digest_kind != "sha256":
                        logging.error("Unsupported digest kind '%s'.", digest_kind)
                    if os.path.exists(asset_path):
                        sha256 = shasum(asset_path)
                        if sha256 == digest_value:
                            logging.debug("Skipping '%s' (matching digests)...", asset_url)
                            continue
                        else:
                            logging.warning("e-downloading '%s' (digests differ)...", asset_url)
                else:
                    if os.path.exists(asset_path):
                        logging.warning("Asset is missing a digest, relying on file size...")
                        size = os.path.getsize(asset_path)
                        if size == asset["size"]:
                            logging.debug("Skipping '%s' (matching sizes)...", asset_url)
                            continue
                        else:
                            logging.warning("Re-downloading '%s' (sizes differ)...", asset_url)

                download_file(asset_url, asset_path)


if __name__ == "__main__":
    main()
