// A zoo of unit conversions within one kind (cross-library parity workload).
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/si.h>
#include <mp-units/systems/usc.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity dist = 42.195 * km;
  std::printf("%f\n", dist.in(m).numerical_value_in(m));
  std::printf("%f\n", dist.in(usc::mile).numerical_value_in(usc::mile));
  std::printf("%f\n", dist.in(usc::yard).numerical_value_in(usc::yard));
  std::printf("%f\n", dist.in(usc::foot).numerical_value_in(usc::foot));
  const quantity t = 2.0 * h + 15.0 * min + 30.0 * s;
  std::printf("%f\n", t.numerical_value_in(s));
  const quantity m2 = 100.0 * kg;
  std::printf("%f\n", m2.numerical_value_in(usc::pound));
}
