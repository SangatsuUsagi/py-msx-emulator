"""Floppy disk controller (FDC) subsystem.

Layered so the three axes that vary across real MSX machines are independent:

- disk image  (``disk_image``): sector-based ``*.dsk`` backing store.
- disk drive  (``disk_drive``): head position + geometry -> logical sector number.
- controller  (``wd2793``):     the FDC chip (registers, command state machine).
- interface   (``interface``):  connection style mapping memory-mapped registers.

A new controller chip or connection style plugs in without touching ``Memory``.
"""
