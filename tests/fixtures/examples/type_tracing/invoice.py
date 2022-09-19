#  This file is part of Pynguin.
#
#  SPDX-FileCopyrightText: 2019–2022 Pynguin Contributors
#
#  SPDX-License-Identifier: LGPL-3.0-or-later
#
from tests.fixtures.examples.type_tracing.large_test_cluster import *  # noqa: F403,F401


def compute_sum(invoice):
    summed = 0
    for item in invoice.iter_items():
        total = item.get_total()
        # Discount
        if total > 100:
            total *= 0.95
        summed += total
    return summed


class Invoice:
    def __init__(self, vat):
        self._items = []

    def add_item(self, item):
        self._items.append(item)

    def iter_items(self):
        yield from self._items


class InvoiceItem:
    def __init__(self, price, amount):
        self._price = price
        self._amount = amount

    def get_total(self):
        return self._price * self._amount
