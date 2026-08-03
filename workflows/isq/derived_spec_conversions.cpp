// Convertibility that cannot be answered by matching an equation literally.
//
// hierarchy_conversions walks a tree of named specs; this one makes the engine explode derived
// definitions and walk EACH INGREDIENT to its root before it can answer. Nothing here matches its
// target recipe as written: `area` is defined over `length`, and what arrives is a `radius`; `speed`
// is `length / time`, and what arrives is a `height` over a `duration`. Every conversion below
// therefore costs a recipe explosion plus a hierarchy walk per ingredient, which is the expensive
// path the rest of the corpus never takes.
//
// The relations are asserted at compile time: a static_assert costs the same engine work as a
// conversion, and stating the negative ones is the point - they are where the walk must *fail*.
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

using namespace mp_units;

// User specs whose equations name *specific* children, so an incoming quantity built from different
// children of the same roots has to be reconciled against them.
QUANTITY_SPEC(patch_area, isq::width* isq::length);
QUANTITY_SPEC(sweep_rate, isq::area / isq::duration);

// Ingredients that are children of what the recipe asks for: radius and height are lengths,
// duration is a time, so each has to be exploded and walked before the answer is known.
static_assert(implicitly_convertible(isq::radius * isq::radius, isq::area));
static_assert(implicitly_convertible(isq::radius * isq::radius * isq::height, isq::volume));
static_assert(implicitly_convertible(isq::height / isq::duration, isq::speed));
static_assert(implicitly_convertible(isq::width * isq::height, isq::area));
static_assert(implicitly_convertible(isq::mass * isq::speed * isq::speed, isq::kinetic_energy));
static_assert(implicitly_convertible(isq::radius * isq::height, patch_area));
static_assert(implicitly_convertible(isq::radius * isq::radius / isq::duration, sweep_rate));

// The walk is directional: a parent does not satisfy a recipe written over a child.
static_assert(!implicitly_convertible(isq::area, isq::radius * isq::radius));
static_assert(explicitly_convertible(isq::area, isq::radius * isq::radius));

// And it respects character: `work` and `moment_of_force` are defined over vector ingredients, so a
// scalar `distance` cannot stand in for them however the tree is walked.
static_assert(!implicitly_convertible(isq::force * isq::distance, isq::work));
static_assert(!implicitly_convertible(isq::distance * isq::force, isq::moment_of_force));

int main()
{
  using namespace mp_units::si::unit_symbols;

  const quantity radius = isq::radius(0.35 * m);
  const quantity height = isq::height(2.4 * m);
  const quantity duration = isq::duration(1.5 * s);
  const quantity mass = isq::mass(12.0 * kg);

  // Each of these is built from children and consumed as the root-level quantity.
  const quantity disc = isq::area(radius * radius);
  const quantity cylinder = isq::volume(radius * radius * height);
  const quantity climb = isq::speed(height / duration);
  const quantity energy = isq::kinetic_energy(mass * climb * climb);

  // Into a user spec whose equation names different children than the ones supplied.
  const quantity patch = patch_area(radius * height);
  const quantity sweep = sweep_rate(radius * radius / duration);

  // Backwards, where the engine refused the implicit walk.
  const quantity back = quantity_cast<isq::radius * isq::radius>(disc);

  std::printf("%f %f %f %f %f %f %f\n", disc.numerical_value_in(m2), cylinder.numerical_value_in(m3),
              climb.numerical_value_in(m / s), energy.numerical_value_in(J),
              patch.numerical_value_in(m2), sweep.numerical_value_in(m2 / s),
              back.numerical_value_in(m2));
}
