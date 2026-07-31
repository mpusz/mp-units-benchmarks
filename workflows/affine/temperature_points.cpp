// REQUIRES: mp-units >= 2.6
// Affine-space temperature handling: offset units, point origins, point conversions.
#include <mp-units/systems/si.h>
#include <mp-units/systems/usc.h>
#include <cstdio>

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity_point room = si::ice_point + delta<deg_C>(21.5);
  const quantity_point body = usc::fahrenheit_zero + delta<usc::degree_Fahrenheit>(98.6);
  const quantity diff = body - room;
  std::printf("%f\n", diff.numerical_value_in(deg_C));
  std::printf("%f\n", room.numerical_value_in(usc::degree_Fahrenheit));
  std::printf("%f\n", body.numerical_value_in(K));
}
