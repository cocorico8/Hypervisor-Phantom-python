#!/usr/bin/env bash

nuitka --onefile --output-filename=hvp-phantom.bin \
              --include-data-dir=patches=patches \
              --include-data-dir=xml=xml \
              auto_hypervisor.py
