// REQUIRES: mp-units >= 2.6
// Vector-character quantities: a representation with tensor order 1, quantity specs declared as
// their own kinds per axis, and decomposition of a whole vector into named component quantities.
// Every other isq/ workflow is scalar, so this is the only one exercising the character machinery.
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
#include <mp-units/utility/cartesian_vector.h>
#endif

namespace flight {

using namespace mp_units;

using vec3 = utility::cartesian_vector<double>;

// One physical vector whose axes carry distinct meanings: each is its own kind, so the type system
// refuses to mix them, while all three remain children of isq::velocity.
QUANTITY_SPEC(flight_velocity, isq::velocity);
QUANTITY_SPEC(forward_velocity, isq::velocity, is_kind);
QUANTITY_SPEC(lateral_velocity, isq::velocity, is_kind);
QUANTITY_SPEC(vertical_velocity, isq::velocity, is_kind);

}  // namespace flight

template<>
struct mp_units::vector_components<flight::flight_velocity> :
    mp_units::vector_axes<flight::forward_velocity, flight::lateral_velocity, flight::vertical_velocity> {};

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  const quantity velocity = flight::vec3{12.0, -3.5, 1.25} * flight::flight_velocity[m / s];
  const quantity wind = flight::vec3{1.5, 0.5, 0.0} * flight::flight_velocity[m / s];
  const quantity ground = velocity + wind;
  const quantity displacement = ground * (30.0 * s);

  const quantity forward = get<flight::forward_velocity>(ground);
  const quantity vertical = get<flight::vertical_velocity>(ground);

  std::printf("%f %f %f\n", forward.numerical_value_in(m / s), vertical.numerical_value_in(m / s),
              displacement.numerical_value_in(m).magnitude());
}
