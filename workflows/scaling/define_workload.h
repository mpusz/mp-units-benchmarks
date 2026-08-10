// Shared workload for the scaling/define_* series: the marginal cost of DEFINING entities, where
// narrow/broad measure the marginal cost of USING them.
//
// The umbrella censuses price a definition too, but against real system headers, whose mix the
// library controls. This series is immune to library growth by construction - the entities are
// defined in the workflow itself - so its slope is the number that can carry the tightest band:
// nothing legitimate moves it except the definition machinery getting slower.
//
// One definition per line, the name and symbol minted from __LINE__, because a definition is a
// declaration and cannot be stamped out by a template loop without changing what is measured - a
// real user writes `inline constexpr struct metre_ final : named_unit<...> {} metre_;` and so does
// this file, one line at a time.
//
// The reference unit is held FIXED for units and specs (the broad lesson: vary one axis - scaling a
// magnitude per step would measure prime factorization, not definition cost). For CONSTANTS the
// magnitude is deliberately VARIED per line, because an arbitrary-rational magnitude is what a
// measured constant IS - that axis is the point, exactly as in constants/codata_expressions.
#pragma once

#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/isq/space_and_time.h>
#include <mp-units/systems/si.h>
#endif

#define BENCH_CAT2(a, b) a##b
#define BENCH_CAT(a, b) BENCH_CAT2(a, b)
#define BENCH_STR2(x) #x
#define BENCH_STR(x) BENCH_STR2(x)

// a named unit equal to the metre, with a distinct name and symbol
#define DEFINE_UNIT                                                                             \
  inline constexpr struct BENCH_CAT(BENCH_CAT(unit, __LINE__), _) final                         \
      : ::mp_units::named_unit<BENCH_STR(BENCH_CAT(u, __LINE__)), ::mp_units::si::metre> {      \
  } BENCH_CAT(unit, __LINE__);

// a leaf quantity spec under isq::length - a plain hierarchy node, no equation (the equation
// premium is priced by the ISQ chapter umbrellas, see findings §25)
#define DEFINE_QUANTITY_SPEC QUANTITY_SPEC(BENCH_CAT(spec, __LINE__), ::mp_units::isq::length);

// a measured constant with a line-dependent rational magnitude - every line a different
// factorization, which is what distinguishes a constant from a unit
#define DEFINE_CONSTANT                                                                          \
  inline constexpr struct BENCH_CAT(BENCH_CAT(constant, __LINE__), _) final                      \
      : ::mp_units::named_constant<BENCH_STR(BENCH_CAT(c, __LINE__)),                            \
                                   ::mp_units::mag_ratio<100 * __LINE__ + 57, 100> *             \
                                       ::mp_units::si::metre> {                                  \
  } BENCH_CAT(constant, __LINE__);
