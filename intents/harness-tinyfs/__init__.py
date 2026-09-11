"""Frozen acceptance harness for the tinyfs intent.
Nothing here is written by the workers who implement tinyfs. The harness knows the
on-disk format from the contract, checks the image byte by byte, drives the command
line as a real process (a crash test needs something to kill), and pumps power-loss
faults into the block device in the middle of a write.
"""
