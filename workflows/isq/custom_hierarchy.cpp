// User-defined quantity hierarchy with defining equations - stresses the definition-time
// convertibility engine the way domain libraries built on mp-units do.
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
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

namespace glide {

using namespace mp_units;

QUANTITY_SPEC(horizontal_length, isq::length);
QUANTITY_SPEC(glide_distance, horizontal_length);
QUANTITY_SPEC(sink_rate, isq::speed, isq::height / isq::duration);
QUANTITY_SPEC(horizontal_speed, isq::speed, horizontal_length / isq::duration);
QUANTITY_SPEC(glide_ratio, dimensionless, horizontal_speed / sink_rate);
QUANTITY_SPEC(specific_power, isq::power / isq::mass);

}  // namespace glide

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity distance = glide::glide_distance(21.0 * km);
  const quantity sink = glide::sink_rate(0.9 * m / s);
  const quantity fwd = glide::horizontal_speed(135.0 * km / h);
  const quantity ratio = glide::glide_ratio(fwd / sink);
  std::printf("%f %f\n", ratio.numerical_value_in(one), distance.numerical_value_in(m));
}
