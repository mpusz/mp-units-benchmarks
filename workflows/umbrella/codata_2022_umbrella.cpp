// REQUIRES: mp-units >= 2.6
// CHURN-EXPECTED: measures the bare cost of ONE full codata adjustment; rebaseline when the tables are
// regenerated. Against codata_lean_umbrella (the 25 essential constants this header includes) the
// difference is what the other ~170 definitions of the same adjustment cost - the number behind the
// header's claim that the full table is worth its own include.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/codata/codata2022.h>
#endif

int main() {}
