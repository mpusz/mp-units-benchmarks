// REQUIRES: mp-units >= 2.6
// Safety ladder, rung 3: quantity points - adds safety level 6 (mathematical space safety) on top
// of the typed rung. The same flight profile, but altitudes are now POSITIONS over a mean-sea-level
// origin and the ISA temperature is a point over si::ice_point: two altitudes cannot be added, a
// temperature difference and an absolute temperature are different types, and the manual "15.0
// minus the drop" arithmetic of the lower rungs becomes point - delta. Read against
// typed_quantities for what level 6 charges; the include set is identical, so the difference is
// pure use-cost.
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

namespace flight {

using namespace mp_units;

inline constexpr struct mean_sea_level final : absolute_point_origin<isq::altitude> {} mean_sea_level;

}  // namespace flight

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;
  using flight::mean_sea_level;

  const quantity_point field_elevation = mean_sea_level + isq::altitude(1650.0 * m);
  const quantity_point cruise_altitude = mean_sea_level + isq::altitude(11'000.0 * m);
  const quantity height_gained = isq::height(cruise_altitude - field_elevation);

  const quantity mass = isq::mass(78'000.0 * kg);
  const quantity true_airspeed = isq::speed(830.0 * km / h);

  const quantity kinetic_energy = isq::kinetic_energy(0.5 * mass * true_airspeed * true_airspeed);
  const quantity g0 = isq::acceleration_of_free_fall(1 * codata::standard_gravity);
  const quantity potential_energy = isq::potential_energy(mass * g0 * height_gained);

  const quantity climb_duration = isq::duration(22.0 * min);
  const quantity vertical_speed = isq::speed(height_gained / climb_duration);

  const quantity lapse_rate = delta<K>(6.5) / (1000.0 * m);
  const quantity_point isa_sea_level_temp = si::ice_point + delta<deg_C>(15.0);
  // level 6 present: the origin is in the type, and point - delta is the only arithmetic that compiles
  const quantity_point temp_at_cruise =
    isa_sea_level_temp - lapse_rate * cruise_altitude.quantity_from(mean_sea_level);

  std::printf("%.1f\n", height_gained.numerical_value_in(m));
  std::printf("%.1f\n", kinetic_energy.numerical_value_in(MJ));
  std::printf("%.1f\n", potential_energy.numerical_value_in(MJ));
  std::printf("%.2f\n", vertical_speed.numerical_value_in(m / s));
  std::printf("%.1f\n", temp_at_cruise.numerical_value_in(deg_C));
}
