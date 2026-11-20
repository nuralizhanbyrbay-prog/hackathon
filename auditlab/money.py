"""Public money rules shared with the agent; contains no episode generator or truth."""
from decimal import Decimal, ROUND_HALF_EVEN

TYPES = ('duplicate_invoice', 'payment_allocation', 'fx_conversion', 'period_cutoff')

def convert(amount, rate, source_exponent=2, target_exponent=2):
    return int((Decimal(amount) * Decimal(rate) * Decimal(10)**(target_exponent-source_exponent)).quantize(Decimal('1'), rounding=ROUND_HALF_EVEN))
