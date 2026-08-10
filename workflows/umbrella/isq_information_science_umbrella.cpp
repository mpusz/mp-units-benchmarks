// REQUIRES: mp-units >= 2.4
// CHURN-EXPECTED: measures the bare cost of the information science and technology chapter;
// rebaseline when it grows. Read against the chapters it includes.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/information_science_and_technology.h>
#endif

int main() {}
