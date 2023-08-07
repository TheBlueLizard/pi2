from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from proof_generation.proof import Proof, Symbol, implies
from proof_generation.proofs.propositional import Propositional

if TYPE_CHECKING:
    from proof_generation.proof import Pattern


@dataclass(frozen=True)
class Assumption(Proof):
    _conclusion: Pattern

    def conclusion(self) -> Pattern:
        return self._conclusion


def test_prove_transitivity() -> None:
    prop = Propositional()
    phi0_implies_phi1 = Assumption(implies(Symbol(0), Symbol(1)))
    phi1_implies_phi2 = Assumption(implies(Symbol(1), Symbol(2)))
    assert prop.imp_transitivity(phi0_implies_phi1, phi1_implies_phi2).conclusion() == implies(Symbol(0), Symbol(2))