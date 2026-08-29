"""Pharmacy service module - inventory and drug interaction helpers."""


def check_low_stock(inventory):
    """Return True if item quantity is at or below reorder level."""
    if inventory is None:
        return False
    return inventory.quantity <= (inventory.reorder_level or 0)
