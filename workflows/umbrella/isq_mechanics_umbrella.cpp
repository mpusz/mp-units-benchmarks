// REQUIRES: mp-units >= 2.2
// CHURN-EXPECTED: measures the bare cost of the mechanics chapter; rebaseline when it grows.
// mechanics includes space_and_time, so its own cost is THIS row minus that one - chapters form a
// dependency chain and only the marginal diff prices a chapter's own specs. Measured 2026-08:
// mechanics' own 43 specs cost ~119 instantiations each against ~49 for space_and_time's, because
// deeper specs name more ingredients in their equations.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/mechanics.h>
#endif

int main() {}
