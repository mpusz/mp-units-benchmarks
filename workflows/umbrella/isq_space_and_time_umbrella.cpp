// REQUIRES: mp-units >= 2.2
// CHURN-EXPECTED: measures the bare cost of the shallowest ISQ chapter; rebaseline when it grows.
// space_and_time depends on no other chapter, so against control/core_only this is the cleanest
// per-quantity-spec price the corpus has - the deeper chapters' specs cost more because their
// equations reference more ingredients, and this row is the floor that difference is read against.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/space_and_time.h>
#endif

int main() {}
