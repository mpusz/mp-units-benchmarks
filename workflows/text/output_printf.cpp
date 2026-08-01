// Quantity text output via printf - the formatting-machinery floor of the text/ family.
// Numerical values are extracted by hand and the units are spelled out in the literals, so no
// library text facility is instantiated at all; the other members of the family are read
// against this baseline.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#include "output_workload.h"

int main()
{
  using namespace mp_units::si::unit_symbols;

  std::printf("%g km/h\n", readings::speed.numerical_value_in(km / h));
  std::printf("%g m/s^2\n", readings::acceleration.numerical_value_in(m / s2));
  std::printf("%g V*A\n", readings::power.numerical_value_in(V * A));
  std::printf("%g J\n", readings::energy.numerical_value_in(J));
}
