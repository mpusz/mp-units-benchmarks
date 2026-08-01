// Shared quantity workload for the text/ output workflows.
//
// Every TU in the family (output_printf, output_ostream, output_format, output_println,
// output_format_advanced) computes exactly these quantities and prints exactly these four
// values, so the differences between those workflows measure the cost of the output facility
// itself - not the quantity arithmetic feeding it.
#pragma once

#include <mp-units/compat_macros.h>
#ifdef MP_UNITS_MODULES
import mp_units;
#else
#include <mp-units/systems/si.h>
#endif

namespace readings {

using namespace mp_units;
using namespace mp_units::si::unit_symbols;

inline constexpr quantity speed = 120.0 * km / h;
inline constexpr quantity acceleration = 9.81 * m / s2;
inline constexpr quantity power = 230.0 * V * (16.0 * A);
inline constexpr quantity energy = (50.0 * kg * (2.0 * m / s) * (2.0 * m / s) / 2.0).in(J);

}  // namespace readings
