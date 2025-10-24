#!/usr/bin/env bash

nuitka --onefile --output-filename=hvp-phantom.bin \
              --include-data-dir=resources=resources \
              auto_hypervisor.py
