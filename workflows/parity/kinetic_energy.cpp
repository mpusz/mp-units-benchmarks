// Basic dimensional arithmetic with mixed units (cross-library parity workload).
#include <mp-units/systems/si.h>
#include <cstdio>

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity mass = 1500.0 * kg;
  const quantity speed = 100.0 * km / h;
  const quantity energy = 0.5 * mass * speed * speed;
  std::printf("%.0f\n", energy.numerical_value_in(kJ));
}
