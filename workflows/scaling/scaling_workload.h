// Shared workload for the scaling/ series.
//
// Every other category measures an intercept: the cost of pulling the library in, plus a handful of
// operations. This one measures the slope - what one more line of user code costs - by compiling the
// same kind of work at several sizes. Cost per operation is the difference between two sizes divided
// by the difference in steps, and a regression in that number means every real file got slower,
// which an intercept measurement cannot tell you.
//
// Two shapes, because they scale for different reasons:
//   narrow - every step works with the same five quantity types, so the library's specializations
//            are already instantiated and the step only adds code. This is what production code
//            looks like: a domain works in a handful of units.
//   broad  - every step uses a different unit, so each one demands new specializations. Less
//            representative, but it is what makes the instantiation table grow.
//
// A step is a function template instantiated once per index rather than a macro repetition: that is
// how the same work would be written by hand, and it keeps the file readable at any size.
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
#include <mp-units/systems/isq/mechanics.h>
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

namespace scaling {

using namespace mp_units;
using namespace mp_units::si::unit_symbols;

// A slice of a real computation: distance and time in, speed, acceleration, force and energy out,
// with the conversions and the kind-safe interfaces a user would actually write.
template<std::size_t I>
constexpr double narrow_step()
{
  const quantity distance = (100.0 + I) * isq::distance[m];
  const quantity duration = (9.5 + I) * isq::duration[s];
  const quantity mass = (1500.0 + I) * isq::mass[kg];
  const quantity speed = distance / duration;
  const quantity acceleration = speed / duration;
  const quantity force = mass * acceleration;
  const quantity energy = force * distance;
  return energy.numerical_value_in(kJ) + speed.numerical_value_in(km / h);
}

// One distinct unit per step, cycling through unrelated corners of SI so each index forces its own
// specializations rather than reusing the previous one's.
template<std::size_t I>
constexpr auto unit_for()
{
  constexpr std::size_t which = I % 8;
  if constexpr (which == 0)
    return m;
  else if constexpr (which == 1)
    return s;
  else if constexpr (which == 2)
    return kg;
  else if constexpr (which == 3)
    return A;
  else if constexpr (which == 4)
    return K;
  else if constexpr (which == 5)
    return mol;
  else if constexpr (which == 6)
    return cd;
  else
    return rad;
}

template<std::size_t I>
constexpr double broad_step()
{
  // A distinct scaled unit per step. Cycling a fixed list of base units would stop producing new
  // specializations once the list is exhausted, which would make this series a copy of the narrow
  // one; scaling by the index keeps every step's type genuinely new.
  constexpr auto unit = mag<I + 1> * unit_for<I>();
  const quantity value = (1.0 + I) * unit;
  const quantity scaled = value * 2.0 + value;
  return scaled.numerical_value_in(unit);
}

template<template<std::size_t> class Step, std::size_t... Is>
constexpr double run(std::index_sequence<Is...>)
{
  return (Step<Is>::value() + ...);
}

template<std::size_t I>
struct narrow {
  static constexpr double value() { return narrow_step<I>(); }
};

template<std::size_t I>
struct broad {
  static constexpr double value() { return broad_step<I>(); }
};

}  // namespace scaling
