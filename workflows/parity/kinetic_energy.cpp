// Basic dimensional arithmetic with mixed units (cross-library parity workload).
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
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity mass = 1500.0 * kg;
  const quantity speed = 100.0 * km / h;
  const quantity energy = 0.5 * mass * speed * speed;
  std::printf("%.0f\n", energy.numerical_value_in(kJ));
}
