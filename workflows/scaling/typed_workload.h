// Shared workload for the scaling/typed_broad_* series: EXACTLY broad's computation - one distinct
// derived quantity composed per step, the same arithmetic, the same (base, base, exponent) triple
// per index - but with TYPED quantities: every reference pairs the unit with its ISQ quantity spec
// (isq::length[si::metre] / isq::time[si::second] instead of m / s), so each step derives a strong
// quantity type and runs the level-5 checking that simple quantities skip. The slope of this shape
// MINUS the slope of `broad` is the price of the typed-quantity abstraction per distinct derived
// quantity - the marginal-cost form of the safety ladder's simple->typed step. Keep the two step
// functions in lockstep: any drift between them contaminates the difference, which is the number
// this shape exists to measure.
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
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

namespace scaling_typed {

using namespace mp_units;

// the same eight bases as broad's base_unit(), each paired with its quantity spec
template<std::size_t I>
constexpr auto base_ref()
{
  constexpr std::size_t which = I % 8;
  if constexpr (which == 0)
    return isq::length[si::metre];
  else if constexpr (which == 1)
    return isq::time[si::second];
  else if constexpr (which == 2)
    return isq::mass[si::kilogram];
  else if constexpr (which == 3)
    return isq::electric_current[si::ampere];
  else if constexpr (which == 4)
    return isq::thermodynamic_temperature[si::kelvin];
  else if constexpr (which == 5)
    return isq::amount_of_substance[si::mole];
  else if constexpr (which == 6)
    return isq::luminous_intensity[si::candela];
  else
    return isq::angular_measure[si::radian];
}

template<std::size_t I>
constexpr auto ref_for()
{
  return base_ref<I % 8>() * pow<1 + (I / 64) % 4>(base_ref<(I / 8) % 8>());
}

template<std::size_t I>
constexpr double typed_step()
{
  constexpr auto ref = ref_for<I>();
  const quantity value = (1.0 + I) * ref;
  const quantity scaled = value * 2.0 + value;
  return scaled.numerical_value_in(scaled.unit);
}

template<std::size_t... Is>
constexpr double run(std::index_sequence<Is...>)
{
  return (typed_step<Is>() + ...);
}

}  // namespace scaling_typed
