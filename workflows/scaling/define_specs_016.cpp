// REQUIRES: mp-units >= 2.2
// Scaling series, definition side: 16 leaf quantity spec definitions under isq::length (plain hierarchy nodes, no equations).
// Read this row against the other sizes of the same shape - the difference divided by the
// difference in steps is the marginal cost of one definition, separated from the constant cost
// of inclusion, which the include twin carries. See define_workload.h for what a step is.
#include "define_workload.h"

namespace defs {
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
DEFINE_QUANTITY_SPEC
}  // namespace defs

int main() {}
