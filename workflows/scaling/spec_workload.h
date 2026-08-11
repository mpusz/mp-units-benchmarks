// Shared workload for the scaling/specs_broad_* series: the marginal cost of COMPOSING a distinct
// derived quantity spec in user code - the quantity-spec counterpart of `broad`, which composes
// derived units. This is the equation pipeline at the USE site: every step forms a spec expression
// a real interface would spell in a constraint (`QuantityOf<isq::mass * pow<2>(isq::length)>`), and
// checks its convertibility the way such a constraint does.
//
// The same lesson as `broad` applies: one axis only. Every step composes from the seven ISQ base
// quantity specs with a small exponent - the (a, b, e) triple is unique for every index below 294,
// so each step demands a spec the framework has never normalized before, and nothing else varies.
#pragma once

#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_IMPORT_STD
import std;
#else
#include <cstddef>
#include <utility>
#endif
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/base_quantities.h>
#endif

namespace scaling_specs {

using namespace mp_units;

template<std::size_t I>
constexpr auto base_spec()
{
  constexpr std::size_t which = I % 7;
  if constexpr (which == 0)
    return isq::length;
  else if constexpr (which == 1)
    return isq::time;
  else if constexpr (which == 2)
    return isq::mass;
  else if constexpr (which == 3)
    return isq::electric_current;
  else if constexpr (which == 4)
    return isq::thermodynamic_temperature;
  else if constexpr (which == 5)
    return isq::amount_of_substance;
  else
    return isq::luminous_intensity;
}

template<std::size_t I>
constexpr auto spec_for()
{
  return base_spec<I % 7>() * pow<1 + (I / 49) % 6>(base_spec<(I / 7) % 7>());
}

template<std::size_t I>
constexpr bool spec_step()
{
  constexpr auto spec = spec_for<I>();
  return implicitly_convertible(spec, spec);  // what a QuantityOf constraint does with it
}

template<std::size_t... Is>
constexpr std::size_t run(std::index_sequence<Is...>)
{
  return (std::size_t{spec_step<Is>()} + ...);
}

}  // namespace scaling_specs
