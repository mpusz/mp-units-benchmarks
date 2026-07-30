// REQUIRES: mp-units >= 2.6
// User-defined absolute and relative point origins with point arithmetic.
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#include <cstdio>

namespace geo {

using namespace mp_units;

inline constexpr struct mean_sea_level final : absolute_point_origin<isq::altitude> {} mean_sea_level;
inline constexpr struct airfield final : relative_point_origin<mean_sea_level + isq::altitude(504 * si::metre)> {} airfield;

using msl_altitude = quantity_point<isq::altitude[si::metre], mean_sea_level>;
using agl_altitude = quantity_point<isq::altitude[si::metre], airfield>;

}  // namespace geo

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const geo::msl_altitude cruise = geo::mean_sea_level + isq::altitude(1500 * m);
  const geo::agl_altitude circuit = geo::airfield + isq::altitude(300 * m);
  const quantity separation = cruise - circuit;
  std::printf("%d %d\n", static_cast<int>(separation.numerical_value_in(m)),
              static_cast<int>((circuit.point_for(geo::mean_sea_level) - geo::mean_sea_level).numerical_value_in(m)));
}
