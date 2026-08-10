// Safety ladder, rung 0: no units library at all. The same flight profile as the other three
// rungs, every quantity a bare double whose unit lives in a comment - and every hazard the ladder
// exists to price is present: manual km/h -> m/s scaling, manual kJ/MJ powers of ten, manual
// temperature-origin arithmetic, and nothing stopping a mass from being added to an altitude.
// This rung is also the adoption-cost control: (simple_quantities minus this file), on both the
// inclusion and the use side, is what STARTING to use mp-units costs a TU - the number every
// evaluation begins with. Deliberately no mp-units include: this TU is what code looks like
// without the library, so it must not pay even the compat header for the privilege.
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif

int main()
{
  const double field_elevation_m = 1650.0;
  const double cruise_altitude_m = 11'000.0;
  const double height_gained_m = cruise_altitude_m - field_elevation_m;

  const double mass_kg = 78'000.0;
  const double true_airspeed_km_h = 830.0;
  const double true_airspeed_m_s = true_airspeed_km_h / 3.6;  // manual unit scaling

  const double kinetic_energy_MJ = 0.5 * mass_kg * true_airspeed_m_s * true_airspeed_m_s / 1e6;
  const double g0_m_s2 = 9.80665;
  const double potential_energy_MJ = mass_kg * g0_m_s2 * height_gained_m / 1e6;

  const double climb_duration_min = 22.0;
  const double vertical_speed_m_s = height_gained_m / (climb_duration_min * 60.0);  // manual again

  const double isa_sea_level_temp_C = 15.0;
  const double lapse_rate_K_per_km = 6.5;
  // manual origin arithmetic: nothing distinguishes this absolute temperature from a difference
  const double temp_at_cruise_C = isa_sea_level_temp_C - lapse_rate_K_per_km * cruise_altitude_m / 1000.0;

  std::printf("%.1f\n", height_gained_m);
  std::printf("%.1f\n", kinetic_energy_MJ);
  std::printf("%.1f\n", potential_energy_MJ);
  std::printf("%.2f\n", vertical_speed_m_s);
  std::printf("%.1f\n", temp_at_cruise_C);
}
