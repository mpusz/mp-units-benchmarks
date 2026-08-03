// Dimensionless quantities and angles, which behave unlike everything else in the corpus: ratios
// collapse to `one`, percent is a scaled `one`, angle is dimensionless yet kind-safe, and the trig
// functions constrain on that. Nothing else here exercises `one`, and a dimensionless result is the
// most common thing a user's arithmetic produces by accident.
#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstdio>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/math.h>
#include <mp-units/systems/angular.h>
#include <mp-units/systems/angular/math.h>  // trig constrained on the angle kind
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity travelled = isq::distance(4.2 * km);
  const quantity planned = isq::distance(5.0 * km);

  // A ratio of like quantities is dimensionless, and reads in several scaled forms of `one`.
  const quantity progress = travelled / planned;
  const quantity as_percent = progress.in(percent);
  const quantity remaining = 1.0 * one - progress;

  // Efficiency-style ratios, where the dimensionless result feeds further arithmetic.
  const quantity gear_ratio = (48.0 * one) / (16.0 * one);
  const quantity combined = progress * gear_ratio;

  // Angle is dimensionless but its own kind, and the trig functions are constrained on it.
  const quantity slope = angular::angle(0.35 * angular::radian);
  const quantity in_degrees = slope.in(angular::degree);
  const quantity vertical = angular::sin(slope) * travelled;
  const quantity horizontal = angular::cos(slope) * travelled;

  // Inverse of a quantity, which lands in dimensionless territory from the other side.
  const quantity rate = 1.0 / isq::duration(2.5 * s);

  std::printf("%f %f %f %f %f %f %f %f\n", progress.numerical_value_in(one),
              as_percent.numerical_value_in(percent), remaining.numerical_value_in(one),
              combined.numerical_value_in(one), in_degrees.numerical_value_in(angular::degree),
              vertical.numerical_value_in(km), horizontal.numerical_value_in(km),
              rate.numerical_value_in(one / s));
}
