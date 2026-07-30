// REQUIRES: mp-units >= 2.6
// The quantity format-spec grammar exercised in depth: component layout (%N/%U/%D/%?), per-component
// format specs (N[...]/U[...]), quantity-level fill/align/width, consteval unit symbol generation
// and a formatted quantity_point. Deliberately not part of the four-way facility comparison - it
// measures the cost of the grammar itself on top of the shared workload.
#include "output_workload.h"
#include <print>

int main()
{
  using namespace mp_units;
  using namespace mp_units::si::unit_symbols;

  // per-component format specs forwarded to the underlying formatters
  std::println("{::N[.2f]}", readings::speed);
  std::println("{::N[.3e]U[n]}", readings::power);
  std::println("{::N[+.1f]}", readings::acceleration);

  // component layout, including the optional number/unit separator and a literal text
  std::println("{:%N in %U}", readings::speed);
  std::println("{:%N%?%U (%D)}", readings::acceleration);
  std::println("{0:%N}|{0:%U}|{0:%D}", readings::energy);

  // quantity-level fill, align and width
  std::println("{:*^24}", readings::energy);
  std::println("{:>16:N[.1f]}", readings::speed);

  // consteval symbol generation with non-default solidus and separator
  std::println("{}", unit_symbol<{.solidus = unit_symbol_solidus::never,
                                  .separator = unit_symbol_separator::half_high_dot}>(kg * m / s2));

  // the affine-space counterpart goes through the same grammar
  const quantity_point room = si::ice_point + delta<deg_C>(21.5);
  std::println("{::N[.1f]}", room);
}
