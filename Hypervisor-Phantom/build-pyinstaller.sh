#!/usr/bin/env bash

pyinstaller --onefile --name hvp-phantom \
              --add-data 'patches:patches' \
              --add-data 'xml:xml' \
              auto_hypervisor.py
