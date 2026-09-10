"""Frozen acceptance harness for the linksvc intent.

This harness is committed to the product repository *before* any worker is
dispatched. Workers may read it, may not modify it (it is outside every write
scope), and are never allowed to grade their own work with it.
"""
