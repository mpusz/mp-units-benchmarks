// REQUIRES: mp-units >= 2.2
// CHURN-EXPECTED: measures the bare cost of the electromagnetism chapter; rebaseline when it grows.
// Read against the chapters it includes (see the header's own #include block for the chain).
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/electromagnetism.h>
#endif

int main() {}
