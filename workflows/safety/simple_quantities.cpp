// REQUIRES: mp-units >= 2.6
// Safety ladder, rung 1: simple quantities (`quantity<unit, Rep>`), the library's natural starting
// point - safety levels 1-4 (dimension, unit, representation, kind) with no extra spelling. The
// same flight profile as raw_doubles: unit conversions are now types, not divisions by 3.6, and a
// mass cannot be added to an altitude. What is still missing shows honestly below: heights, speeds
// and energies are interchangeable within their kinds (level 5), and the ISA temperature is still
// manual origin arithmetic on a bare number (level 6). Read against raw_doubles for the adoption
// cost, and against typed_quantities for what level 5 charges.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/codata/adopted_values.h>
#include <mp-units/systems/si.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity field_elevation = 1650.0 * m;
  const quantity cruise_altitude = 11'000.0 * m;
  const quantity height_gained = cruise_altitude - field_elevation;

  const quantity mass = 78'000.0 * kg;
  const quantity true_airspeed = 830.0 * km / h;  // stays in km/h; conversions are types now

  const quantity kinetic_energy = 0.5 * mass * true_airspeed * true_airspeed;
  const quantity g0 = 1 * codata::standard_gravity;
  const quantity potential_energy = mass * g0 * height_gained;

  const quantity climb_duration = 22.0 * min;
  const quantity vertical_speed = height_gained / climb_duration;

  const quantity lapse_rate = delta<K>(6.5) / (1000.0 * m);
  // level 6 still missing: the origin lives in the programmer's head, not in the type
  const double temp_at_cruise_C = 15.0 - (lapse_rate * cruise_altitude).numerical_value_in(K);

  std::printf("%.1f\n", height_gained.numerical_value_in(m));
  std::printf("%.1f\n", kinetic_energy.numerical_value_in(MJ));
  std::printf("%.1f\n", potential_energy.numerical_value_in(MJ));
  std::printf("%.2f\n", vertical_speed.numerical_value_in(m / s));
  std::printf("%.1f\n", temp_at_cruise_C);
}
