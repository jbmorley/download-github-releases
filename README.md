# download-github-releases

Script to download all the releases for GitHub projects

## Usage

```plaintext
usage: download-github-releases.py [-h] [--verbose] [--download-source]
                                   --output OUTPUT
                                   repository [repository ...]

Download (and keep in sync) the releases from a set of GitHub projects.

positional arguments:
  repository            GitHub repository to fetch releases from

options:
  -h, --help            show this help message and exit
  --verbose, -v         show verbose output
  --download-source, -s
                        also download source for each release
  --output OUTPUT       output directory

Set the GITHUB_TOKEN environment variable to use token-based authentication.
This is required for accessing private repositories and can help with API rate
limits.
```
