# Copyright 2014 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

netrc = netrc()

credentials(
  name = 'netrc',
  username=netrc.getusername,
  password=netrc.getpassword)

python_library(
  name = 'authentication',
  sources = globs('*.py'),
  dependencies = [
    'src/python/pants/base:build_file_aliases',
    'src/python/pants/base:build_manual',
  ]
)
