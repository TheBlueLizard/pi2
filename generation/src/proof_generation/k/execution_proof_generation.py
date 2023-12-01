from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import proof_generation.proof as proof
import proof_generation.proofs.kore as kl
from proof_generation.k.kore_convertion.language_semantics import (
    AxiomType,
    ConvertedAxiom,
    KEquationalRule,
    KRewritingRule,
)
from proof_generation.pattern import Symbol
from proof_generation.proofs.definedness import functional
from proof_generation.proofs.substitution import Substitution

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType

    from proof_generation.k.kore_convertion.language_semantics import LanguageSemantics
    from proof_generation.k.kore_convertion.rewrite_steps import RewriteStepExpression
    from proof_generation.pattern import Pattern


Location = tuple[int, ...]

@dataclass
class SimplificationInfo:
    location: Location
    initial_pattern: Pattern  # Relative to previous in stack
    simplification_result: Pattern
    simplifications_left: int


class SimplificationVisitor:
    def __init__(self, language_semantics: LanguageSemantics, init_config: Pattern):
        self._language_semantics = language_semantics
        self._curr_config = init_config
        self._simplification_stack: list[SimplificationInfo] = []
        self._in_simplification = False

    @property
    def simplified_configuration(self) -> Pattern:
        return self._curr_config

    def update_configuration(self, new_current_configuration: Pattern) -> None:
        assert not self._simplification_stack, 'Simplification stack is not empty'
        assert not self._in_simplification, 'Simplification is in progress'
        self._curr_config = new_current_configuration

    def __call__(self, ordinal: int, substitution: dict[int, Pattern], location: Location) -> SimplificationInfo:
        assert self._in_simplification, 'Simplification is not in progress'

        # Check that whether it is the first simplification or not
        if not self._simplification_stack:
            sub_pattern = self.get_subpattern(location, self._curr_config)
        else:
            sub_pattern = self.get_subpattern(location, self._simplification_stack[-1].simplification_result)

        # Get the rule and remove extra variables added by K in addition to the ones in the K file
        rule = self._language_semantics.get_axiom(ordinal)
        assert isinstance(rule, KEquationalRule), 'Simplification rule is not equational'
        base_substitutions = rule.substitutions_from_requires
        simplified_rhs = self.apply_substitutions(rule.right, base_substitutions)

        # Now apply the substitutions from the hint
        simplified_rhs = self.apply_substitutions(simplified_rhs, substitution)

        # Count the number of substitutions left
        simplifications_left = self._language_semantics.count_simplifications(simplified_rhs)

        # Create the new info object and put it on top of the stack
        new_info = SimplificationInfo(location, sub_pattern, simplified_rhs, simplifications_left)
        self._simplification_stack.append(new_info)
        
        return new_info

    def __enter__(self) -> SimplificationVisitor:
        assert not self._in_simplification
        self._in_simplification = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        while self._simplification_stack and self._simplification_stack[-1].simplifications_left == 0:
            exhausted_info = self._simplification_stack.pop()
            if self._simplification_stack:
                # If the stack is non-empty, then we need to update the simplification on top of the stack
                last_info = self._simplification_stack[-1]
                last_info.simplifications_left -= 1
                last_info.simplification_result = self.update_subterm(
                    exhausted_info.location, last_info.simplification_result, exhausted_info.simplification_result
                )
            else:
                # If the stack is empty, then we need to update the current configuration as we processed the batch
                self.curr_config = self.update_subterm(
                    exhausted_info.location, self._curr_config, exhausted_info.simplification_result
                )

    @staticmethod
    def get_subpattern(location: Location, pattern: Pattern) -> Pattern:
        raise NotImplementedError

    @staticmethod
    def update_subterm(location: Location, pattern: Pattern, plug: Pattern) -> Pattern:
        raise NotImplementedError

    @staticmethod
    def apply_substitutions(pattern: Pattern, substitutions: dict[int, Pattern]) -> Pattern:
        for evar_name, plug in substitutions.items():
            pattern = pattern.apply_esubst(evar_name, plug)
        return pattern


class ExecutionProofExp(proof.ProofExp):
    def __init__(self, language_semantics: LanguageSemantics, init_config: Pattern):
        self._init_config = init_config
        self._curr_config = init_config
        self.language_semantics = language_semantics
        super().__init__(notations=list(language_semantics.notations))
        self.subst_proofexp = self.import_module(Substitution())
        self.kore_lemmas = self.import_module(kl.KoreLemmas())
        self._simplification_visitor = SimplificationVisitor(self.language_semantics, self.current_configuration)

    @property
    def initial_configuration(self) -> Pattern:
        """Returns the initial configuration."""
        return self._init_config

    @staticmethod
    def collect_functional_axioms(
        language_semantics: LanguageSemantics, substitutions: dict[int, Pattern]
    ) -> list[ConvertedAxiom]:
        subst_axioms = []
        for pattern in substitutions.values():
            # Doublecheck that the pattern is a functional symbol and it is valid to generate the axiom
            sym, _ = kl.deconstruct_nary_application(pattern)
            assert isinstance(sym, Symbol), f'Pattern {pattern} is not supported'
            k_sym = language_semantics.resolve_to_ksymbol(sym)
            assert k_sym is not None
            assert k_sym.is_functional
            converted_pattern = functional(pattern)
            subst_axioms.append(ConvertedAxiom(AxiomType.FunctionalSymbol, converted_pattern))
        return subst_axioms

    def add_assumptions_for_rewrite_step(self, rule: KRewritingRule, substitutions: dict[int, Pattern]) -> None:
        """Add axioms to the definition."""
        # TODO: We don't use them until the substitutions are implemented
        func_axioms = ExecutionProofExp.collect_functional_axioms(self.language_semantics, substitutions)
        self.add_assumptions([axiom.pattern for axiom in func_axioms])
        self.add_axiom(rule.pattern)

    @property
    def current_configuration(self) -> Pattern:
        """Returns the current configuration."""
        return self._curr_config

    def rewrite_event(self, rule: KRewritingRule, substitution: dict[int, Pattern]) -> proof.ProofThunk:
        """Extends the proof with an additional rewrite step."""
        # Check that the rule is krewrites
        instantiated_axiom = rule.pattern.instantiate(substitution)
        match = kl.kore_rewrites.assert_matches(instantiated_axiom)
        lhs = match[1]
        rhs = match[2]

        # Check that the lhs matches the current configuration
        assert (
            lhs == self.current_configuration
        ), f'The current configuration {lhs.pretty(self.pretty_options())} does not match the lhs of the rule {rule.pattern.pretty(self.pretty_options())}'

        # Add the axioms
        self.add_assumptions_for_rewrite_step(rule, substitution)

        # Add the claim
        self.add_claim(instantiated_axiom)

        # Add the proof
        proof = self.dynamic_inst(self.load_axiom(rule.pattern), substitution)
        self.add_proof_expression(proof)
        self._curr_config = rhs

        # Update the current configuration
        self._simplification_visitor.update_configuration(self._curr_config)

        return proof

    def simplification_event(self, ordinal: int, substitution: dict[int, Pattern], location: Location) -> None:
        with self._simplification_visitor as visitor:
            visitor(ordinal, substitution, location)
            # TODO: Do some proving here ...

        # Update the current configuration
        self._curr_config = self._simplification_visitor.simplified_configuration

    def finalize(self) -> None:
        """Prepare proof expression for the final reachability claim"""
        # TODO: Prove the final reachability claim
        return

    @staticmethod
    def from_proof_hints(
        hints: Iterator[RewriteStepExpression], language_semantics: LanguageSemantics
    ) -> proof.ProofExp:
        """Constructs a proof expression from a list of rewrite hints."""
        proof_expr: ExecutionProofExp | None = None
        for hint in hints:
            if proof_expr is None:
                proof_expr = ExecutionProofExp(language_semantics, hint.configuration_before)

            if isinstance(hint.axiom, KRewritingRule):
                proof_expr.rewrite_event(hint.axiom, hint.substitutions)
            else:
                # TODO: Remove the stub
                raise NotImplementedError('TODO: Add support for equational rules')

        if proof_expr is None:
            print('WARNING: The proof expression is empty, ho hints were provided.')
            return proof.ProofExp(notations=list(language_semantics.notations))
        else:
            return proof_expr
