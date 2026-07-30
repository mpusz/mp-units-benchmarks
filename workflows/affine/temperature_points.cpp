// REQUIRES: mp-units >= 2.6
// Affine-space temperature handling: offset units, point origins, point conversions.
#include <mp-units/systems/si.h>
#include <mp-units/systems/usc.h>
#include <cstdio>

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity_point room = si::ice_point + 21.5 * deg_C;
  const quantity_point body = usc::fahrenheit_zero + 98.6 * usc::degree_Fahrenheit;
  const quantity diff = body - room;
  std::printf("%f\n", diff.numerical_value_in(deg_C));
  std::printf("%f\n", room.numerical_value_in(usc::degree_Fahrenheit));
  std::printf("%f\n", body.numerical_value_in(K));
}
