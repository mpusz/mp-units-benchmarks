// REQUIRES: mp-units >= 2.6
// Safety ladder, rung 2: typed quantities (`quantity<isq::spec[unit], Rep>`) - adds safety level 5
// (quantity safety) to the simple rung. The same flight profile, but heights, altitudes, speeds
// and the two energies are now DISTINCT types with their ISQ equations enforced: kinetic energy
// must be formed from a mass and a speed, potential energy from a mass, an acceleration of free
// fall and a height, and passing one where the other is expected does not compile. Read against
// simple_quantities for what level 5 charges - this rung also pays the ISQ mechanics chapter's
// inclusion, which the include twin keeps out of the use-cost. Level 6 is still missing: the ISA
// temperature stays manual, which affine_quantities fixes.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/mechanics.h>
#include <mp-units/systems/codata/adopted_values.h>
#include <mp-units/systems/si.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity field_elevation = isq::altitude(1650.0 * m);
  const quantity cruise_altitude = isq::altitude(11'000.0 * m);
  const quantity height_gained = isq::height(cruise_altitude - field_elevation);

  const quantity mass = isq::mass(78'000.0 * kg);
  const quantity true_airspeed = isq::speed(830.0 * km / h);

  const quantity kinetic_energy = isq::kinetic_energy(0.5 * mass * true_airspeed * true_airspeed);
  const quantity g0 = isq::acceleration_of_free_fall(1 * codata::standard_gravity);
  const quantity potential_energy = isq::potential_energy(mass * g0 * height_gained);

  const quantity climb_duration = isq::duration(22.0 * min);
  const quantity vertical_speed = isq::speed(height_gained / climb_duration);

  const quantity lapse_rate = delta<K>(6.5) / (1000.0 * m);
  // level 6 still missing: the origin lives in the programmer's head, not in the type
  const double temp_at_cruise_C = 15.0 - (lapse_rate * cruise_altitude).numerical_value_in(K);

  std::printf("%.1f\n", height_gained.numerical_value_in(m));
  std::printf("%.1f\n", kinetic_energy.numerical_value_in(MJ));
  std::printf("%.1f\n", potential_energy.numerical_value_in(MJ));
  std::printf("%.2f\n", vertical_speed.numerical_value_in(m / s));
  std::printf("%.1f\n", temp_at_cruise_C);
}
