"""C6 / RFC-055 — family takaful: the split, the option, and the loan.

Three things carry the weight here, and only the first is ordinary
arithmetic.

The **three-way split** of a contribution must close exactly, not to
tolerance, because the tabarru' is written as the residual and a fund that
receives a rounding error every period is a fund whose surplus is partly an
artefact.

The **mudarabah share is an option, not a fee**. A mudarib shares profit and
does not share loss, so the operator's take is
``share × max(earned, 0)``. Hold the mean return fixed, raise the
volatility, and the operator earns more while the participants earn less by
exactly as much. That is a property of the contract and it is invisible in
any deterministic projection — so it is measured against a hand-built
two-point scenario set, where the numbers can be written down.

The **qard hasan** is the mechanism the product would not work without, and
the one with the finding attached. It is drawn to exactly the shortfall,
repaid only out of surplus and ahead of any distribution, and what the
priority moves between generations of participants is computed on purpose
so it can be read rather than rediscovered.

Executor class, stated and asserted: ``FamilyTakaful`` declares ``@pool``
variables, so it is in RFC-061's **block** class — ``run`` over a block of
more than one must raise ``PooledBlockError``, and the bridge into the
per-policy class is a block of one. Bound to a scenario set it is *also* in
RFC-068's scenario class, which is why the worked example runs under the
stochastic executor alone. It is the second template in both (with
``VariablePayoutAnnuity``), and the tests below hold it to both bridges.
"""

import numpy as np
import pytest

from engine.core.registry import record_run
from engine.core.runner import PooledBlockError
from engine.core.runner import run as run_interpreted
from engine.core.stochastic import run_stochastic
from engine.core.vector import run_vectorized
from engine.data.assumptions import Assumptions, MortalityTable
from engine.data.modelpoints import ModelPoint, from_dicts
from engine.data.scenarios import ScenarioSet
from engine.library.takaful import FamilyTakaful

Q = 0.01
I = 0.04


def assumptions(**over):
    kw = dict(mortality=MortalityTable.flat(Q), lapse=0.0, interest=I)
    kw.update(over)
    return Assumptions(**kw)


def one(**over):
    """One model point whose tabarru' covers its claims with room to spare.

    A contribution of 2,000 leaves 560 of tabarru' after the wakala fee and
    the savings split; at ``Q = 0.01`` a sum covered of 40,000 costs 400.
    The fund therefore runs a surplus on a flat basis, which is what makes a
    *deficit* fixture below say something — an under-priced fund would draw
    a qard for a reason that has nothing to do with the mechanism under
    test."""
    kw = dict(id="K1", age_at_entry=45, term_years=10, sum_covered=40_000.0,
              annual_contribution=2_000.0, init_pols=1_000.0)
    kw.update(over)
    return ModelPoint(**kw)


def block():
    """Two model points, so a pooled variable has something to pool."""
    return [one(), one(id="K2", age_at_entry=55, term_years=8,
                       sum_covered=25_000.0, annual_contribution=1_200.0,
                       init_pols=400.0)]


def run(model_cls=FamilyTakaful, points=None, a=None, n=11, outputs=None,
        scenarios=None):
    points = block() if points is None else points
    a = assumptions() if a is None else a
    if scenarios is None:
        return run_vectorized(model_cls, points, a, n, outputs=outputs)
    return run_stochastic(model_cls, points, a, scenarios, n, outputs=outputs)


# --- 1. the contribution splits three ways, exactly ------------------------


def test_the_three_way_split_closes_exactly():
    """`==`, not `approx`. The tabarru' is written as the residual precisely
    so that this holds bit for bit; a fund that receives a rounding error
    every period would have a surplus that is partly an artefact of the
    arithmetic rather than of the experience."""
    result = run(outputs=["contribution", "wakala_fee_charged", "savings_in",
                          "tabarru"])
    got = {n: np.asarray(result.array(n)) for n in
           ("contribution", "wakala_fee_charged", "savings_in", "tabarru")}
    assert np.array_equal(
        got["contribution"],
        got["wakala_fee_charged"] + got["savings_in"] + got["tabarru"],
    )


def test_the_wakala_fee_is_the_stated_share_of_the_gross_contribution():
    """Of the **gross**, which is the point of an agency fee: it is not a
    charge on the fund and it does not come back in any surplus."""
    result = run(points=[one()], outputs=["contribution",
                                          "wakala_fee_charged", "tabarru"])
    contribution = np.asarray(result.array("contribution"))[0, 0]
    fee = np.asarray(result.array("wakala_fee_charged"))[0, 0]
    tabarru = np.asarray(result.array("tabarru"))[0, 0]
    assert contribution == 2_000.0
    assert fee == pytest.approx(2_000.0 * FamilyTakaful.wakala_fee)
    # 2000 gross, 600 fee, 1400 net, 60% saved, so 560 donated.
    assert tabarru == pytest.approx(1_400.0 * (1 - FamilyTakaful.savings_share))


def test_a_pure_wakala_plan_and_a_pure_mudarabah_plan_are_the_same_template():
    """Two models of the same product, and neither needs a branch. Setting
    `wakala_fee` to zero gives pure mudarabah; setting both profit shares to
    zero gives pure wakala. A template with an `if model == ...` in it would
    have two code paths where the contract has two numbers."""

    class PureWakala(FamilyTakaful):
        wakala_fee = 0.30
        mudarabah_share = 0.0
        operator_surplus_share = 0.0

    class PureMudarabah(FamilyTakaful):
        wakala_fee = 0.0
        mudarabah_share = 0.20
        operator_surplus_share = 0.30

    wakala = run(PureWakala, outputs=["wakala_fee_charged",
                                      "fund_mudarabah_fee",
                                      "surplus_to_operator"])
    assert np.asarray(wakala.array("wakala_fee_charged")).sum() > 0
    assert np.asarray(wakala.array("fund_mudarabah_fee")).sum() == 0.0
    assert np.asarray(wakala.array("surplus_to_operator")).sum() == 0.0

    mudarabah = run(PureMudarabah, outputs=["wakala_fee_charged",
                                            "fund_mudarabah_fee",
                                            "surplus_to_operator"])
    assert np.asarray(mudarabah.array("wakala_fee_charged")).sum() == 0.0
    assert np.asarray(mudarabah.array("surplus_to_operator")).sum() > 0


# --- 2. the mudarabah share is a call option -------------------------------


def _two_point(up, down, periods):
    """Two scenarios, mirror images, so their mean return is exactly zero
    and every number below can be written down by hand."""
    return ScenarioSet(np.array([[up] * periods, [down] * periods]))


def test_the_operator_shares_profit_and_not_loss():
    """The definition, checked at one period. Earned +20% and −20%; a 25%
    mudarabah share takes 5 points of the first and none of the second."""

    class Quarter(FamilyTakaful):
        mudarabah_share = 0.25

    result = run(Quarter, points=[one()], n=3,
                 scenarios=_two_point(0.20, -0.20, 4),
                 outputs=["earned_rate", "participant_rate"])
    earned = np.asarray(result.array("earned_rate"))[0, 0]
    kept = np.asarray(result.array("participant_rate"))[0, 0]
    assert earned.tolist() == [0.20, -0.20]
    assert kept == pytest.approx([0.15, -0.20])


def test_the_asymmetry_costs_the_participants_exactly_what_it_pays_the_operator():
    """Nothing is created or destroyed by the split — the operator's gain is
    the participants' loss, to the last bit. Asserted with `==` because both
    sides are the same subtraction written twice."""
    result = run(points=[one()], n=3, scenarios=_two_point(0.20, -0.20, 4),
                 outputs=["earned_rate", "participant_rate"])
    earned = np.asarray(result.array("earned_rate"))
    kept = np.asarray(result.array("participant_rate"))
    operator_take = earned - kept
    assert np.array_equal(kept + operator_take, earned)
    # And the take is never negative: a loss is the participants' alone.
    assert (operator_take >= 0.0).all()


def test_volatility_at_a_fixed_mean_moves_money_to_the_operator():
    """The consequence nobody quotes, and the reason the option framing is
    not decoration. Both scenario sets have mean return exactly zero. The
    wider one pays the operator strictly more and the participants strictly
    less, on a contract whose terms have not changed at all.

    This is the same shape as `MonthlySum`'s cap in
    `engine/data/index_credit.py`: an asymmetry that is invisible in any
    deterministic projection and is the whole economics of the design."""
    narrow = run(points=[one()], n=3, scenarios=_two_point(0.05, -0.05, 4),
                 outputs=["earned_rate", "participant_rate"])
    wide = run(points=[one()], n=3, scenarios=_two_point(0.40, -0.40, 4),
               outputs=["earned_rate", "participant_rate"])

    def take(res):
        earned = np.asarray(res.array("earned_rate"))[0, 0]
        kept = np.asarray(res.array("participant_rate"))[0, 0]
        return float((earned - kept).mean())

    assert np.asarray(narrow.array("earned_rate"))[0, 0].mean() == 0.0
    assert np.asarray(wide.array("earned_rate"))[0, 0].mean() == 0.0
    assert take(wide) > take(narrow) > 0.0
    # And it is exactly proportional: half the share of half the upside.
    assert take(wide) == pytest.approx(8.0 * take(narrow))


# --- 3. qard hasan ---------------------------------------------------------


def _bad_then_good(periods=7):
    """One scenario: a −50% investment year, then +40% for the rest.

    A well-priced fund driven into deficit by the markets rather than by its
    underwriting, which is the position a qard facility actually exists for
    and the only way to reach a *repayment* under a flat mortality basis —
    a fund that is in deficit because its tabarru' is too thin is in deficit
    for good, and never repays anything.
    """
    return ScenarioSet(np.array([[-0.50] + [0.40] * (periods - 1)]))


def _deficit_block():
    """A fund priced far below its claims, so it is in deficit from the
    first period and stays there — the position of a new takaful window
    whose tabarru' is too thin, and the only way to see a qard drawn under
    a deterministic basis."""
    return [one(sum_covered=400_000.0, annual_contribution=1_000.0)]


def test_the_loan_is_exactly_the_shortfall_and_not_a_penny_more():
    """An operator that lent a buffer would be capitalising the fund rather
    than rescuing it, and the surplus that buffer earned would go to
    participants who had not provided it."""
    result = run(points=_deficit_block(), n=6,
                 outputs=["risk_fund_after_experience", "qard_drawn",
                          "risk_fund_after_qard"])
    shortfall = np.asarray(result.array("risk_fund_after_experience"))[:, 0]
    drawn = np.asarray(result.array("qard_drawn"))[:, 0]
    after = np.asarray(result.array("risk_fund_after_qard"))[:, 0]
    assert (shortfall[:5] < 0).all()
    assert np.array_equal(drawn, np.maximum(-shortfall, 0.0))
    # And the fund lands on zero by construction rather than by a clip.
    assert np.array_equal(after[shortfall < 0], np.zeros((shortfall < 0).sum()))


def test_a_fund_in_surplus_borrows_nothing():
    result = run(n=11, outputs=["risk_fund_after_experience", "qard_drawn",
                                "qard_outstanding"])
    assert (np.asarray(result.array("risk_fund_after_experience")) >= 0).all()
    assert np.asarray(result.array("qard_drawn")).sum() == 0.0
    assert np.asarray(result.array("qard_outstanding")).sum() == 0.0


def test_the_loan_is_repaid_out_of_surplus_and_never_more_than_is_owed():
    """Bounded twice, by what is outstanding and by what the fund has.
    Either bound alone permits a nonsense: repaying more than was borrowed,
    or repaying out of a fund that is still in deficit."""
    result = run(points=[one()], n=6, scenarios=_bad_then_good(),
                 outputs=["qard_outstanding_boy", "qard_repaid", "qard_drawn",
                          "risk_fund_after_experience", "qard_outstanding"])
    g = {n: np.asarray(result.array(n))[:, 0, 0] for n in
         ("qard_outstanding_boy", "qard_repaid", "qard_drawn",
          "risk_fund_after_experience", "qard_outstanding")}
    assert g["qard_drawn"].sum() > 0, "the fixture must draw a loan"
    assert g["qard_repaid"].sum() > 0, "and must repay some of it"
    assert (g["qard_repaid"] <= g["qard_outstanding_boy"] + 1e-9).all()
    assert (g["qard_repaid"]
            <= np.maximum(g["risk_fund_after_experience"], 0.0) + 1e-9).all()
    assert (g["qard_outstanding"] >= -1e-9).all()


def test_the_fund_balances_period_by_period():
    """Opening, plus everything in, less everything out, is next period's
    opening — with the loan's two movements in the right places. Exact:
    every term is a float already computed, and an identity that only holds
    to tolerance is an identity with an unmodelled leak in it."""
    names = ["risk_fund_boy", "tabarru_received", "fund_investment_income",
             "claims_paid", "qard_drawn", "qard_repaid",
             "distributable_surplus", "risk_fund_after_qard"]
    result = run(points=[one()], n=6, scenarios=_bad_then_good(),
                 outputs=names)
    g = {n: np.asarray(result.array(n))[:, 0, 0] for n in names}
    closing = (g["risk_fund_boy"] + g["tabarru_received"]
               + g["fund_investment_income"] - g["claims_paid"]
               + g["qard_drawn"] - g["qard_repaid"])
    assert np.array_equal(closing, g["risk_fund_after_qard"])
    assert np.array_equal(
        g["risk_fund_boy"][1:],
        (g["risk_fund_after_qard"] - g["distributable_surplus"])[:-1],
    )


def test_the_fund_after_the_loan_is_never_negative():
    result = run(points=_deficit_block(), n=8,
                 outputs=["risk_fund_after_qard", "distributable_surplus"])
    assert (np.asarray(result.array("risk_fund_after_qard")) >= 0.0).all()
    assert (np.asarray(result.array("distributable_surplus")) >= 0.0).all()


def test_an_unrepaid_loan_is_the_operators_realised_loss():
    """There is no other repayment route and the loan carries no return, so
    a run-off that ends with a balance outstanding ends with the operator
    out of pocket by exactly that amount. Asserting it here is what stops
    the closing balance being read as a receivable."""
    result = run(points=_deficit_block(), n=12,
                 outputs=["qard_drawn", "qard_repaid", "qard_outstanding"])
    drawn = np.asarray(result.array("qard_drawn"))[:, 0]
    repaid = np.asarray(result.array("qard_repaid"))[:, 0]
    outstanding = np.asarray(result.array("qard_outstanding"))[:, 0]
    assert repaid.sum() == 0.0
    assert outstanding[-1] == pytest.approx(drawn.sum())
    assert outstanding[-1] > 0.0


# --- 4. the finding: what the repayment priority moves ---------------------


def test_the_generational_transfer_is_the_distribution_rate_times_the_repayment():
    """The counterfactual is computed on purpose, and it collapses to
    something small and exact — which is *why* the transfer is invisible in
    an operator's accounts. It does not look like a payment from one
    generation to another; it looks like a slightly smaller distribution.

    `surplus_if_qard_ignored − distributable_surplus` is
    `distribution_rate × qard_repaid` identically, in surplus and in
    deficit alike, and `qard_transfer_to_participants` is the participants'
    share of it."""
    names = ["surplus_if_qard_ignored", "distributable_surplus",
             "qard_repaid", "qard_transfer_to_participants"]
    result = run(points=[one()], n=6, scenarios=_bad_then_good(),
                 outputs=names)
    g = {n: np.asarray(result.array(n))[:, 0, 0] for n in names}
    gap = g["surplus_if_qard_ignored"] - g["distributable_surplus"]
    assert gap == pytest.approx(FamilyTakaful.distribution_rate
                                * g["qard_repaid"])
    assert g["qard_transfer_to_participants"] == pytest.approx(
        gap * (1.0 - FamilyTakaful.operator_surplus_share))
    assert gap.sum() > 0.0, "the fixture must exercise a repayment"


def test_a_fund_that_never_borrows_has_no_transfer_to_report():
    """The counterfactual has to be zero where the rule does not bite, or it
    would be reporting a transfer on every fund that ever distributed
    anything."""
    result = run(n=11, outputs=["surplus_if_qard_ignored",
                                "distributable_surplus",
                                "qard_transfer_to_participants"])
    assert np.array_equal(
        np.asarray(result.array("surplus_if_qard_ignored")),
        np.asarray(result.array("distributable_surplus")),
    )
    assert np.asarray(
        result.array("qard_transfer_to_participants")).sum() == 0.0


# --- 5. surplus distribution ----------------------------------------------


def test_the_allocation_gives_out_the_surplus_and_no_more():
    """Summed over the block, what the participants are paid plus what could
    not be allocated is what was declared for them. An allocation that did
    not close would be creating or destroying money in the one place a
    takaful fund cannot afford it."""
    result = run(n=11, outputs=["surplus_paid", "surplus_to_participants",
                                "unallocated_surplus"])
    paid = np.asarray(result.array("surplus_paid")).sum(axis=1)
    stranded = np.asarray(result.array("unallocated_surplus"))[:, 0]
    declared = np.asarray(result.array("surplus_to_participants"))[:, 0]
    assert paid + stranded == pytest.approx(declared)


def test_the_fund_outlives_the_participants_and_says_so():
    """A residual nobody has a claim on, reported rather than allocated.

    Every contract in this block runs off by period 10; the fund still
    holds a balance and the distribution rule still releases a share of it.
    The contract does not say who gets that, and practice does not agree —
    charity, the next generation's fund, or the operator — so the template
    reports it and refuses to invent a rule.

    Asserted in both directions: nothing is stranded while participants
    remain, and everything declared is stranded once none do."""
    result = run(n=13, outputs=["surplus_paid", "unallocated_surplus",
                                "surplus_to_participants", "allocation_base"])
    base = np.asarray(result.array("allocation_base"))[:, 0]
    stranded = np.asarray(result.array("unallocated_surplus"))[:, 0]
    declared = np.asarray(result.array("surplus_to_participants"))[:, 0]
    paid = np.asarray(result.array("surplus_paid")).sum(axis=1)
    assert (base > 0).any() and (base == 0).any(), "the fixture must run off"
    assert (stranded[base > 0] == 0.0).all()
    assert stranded[base == 0] == pytest.approx(declared[base == 0])
    assert (paid[base == 0] == 0.0).all()
    assert stranded.sum() > 0.0


def test_surplus_follows_the_tabarru_donated_and_therefore_the_duration():
    """Pro rata to tabarru' donated to date, which is as much of the timing
    question as an allocation rule can carry. What it cannot carry is the
    qard — see the module docstring, and the third instance of that limit.

    Two participants donating at different rates, one of whose cover ends
    first. The share ratio has to track the cumulative-donation ratio at
    every period *and* to move when the short contract stops donating —
    a rule that allocated on the current period's tabarru', or on head
    count, would pass the first check and fail the second."""
    big = one(id="BIG", annual_contribution=3_000.0, term_years=10)
    short = one(id="SHORT", annual_contribution=1_000.0, term_years=4)
    result = run(points=[big, short], n=9,
                 outputs=["surplus_share_per_pol", "tabarru_paid"])
    share = np.asarray(result.array("surplus_share_per_pol"))
    donated = np.asarray(result.array("tabarru_paid"))
    live = donated[:, 1] > 0
    assert (share[live, 0] / share[live, 1]
            == pytest.approx(donated[live, 0] / donated[live, 1]))
    # The short contract stops donating at period 4, so from there the ratio
    # widens every period. A rule blind to duration would hold it flat.
    ratio = donated[:, 0] / donated[:, 1]
    assert ratio[3] == pytest.approx(3.0)
    assert (np.diff(ratio[3:]) > 0).all()


def test_the_operators_performance_fee_comes_out_of_the_same_surplus():
    class Sharing(FamilyTakaful):
        operator_surplus_share = 0.40

    result = run(Sharing, n=11, outputs=["distributable_surplus",
                                         "surplus_to_operator",
                                         "surplus_to_participants"])
    total = np.asarray(result.array("distributable_surplus"))
    operator = np.asarray(result.array("surplus_to_operator"))
    participants = np.asarray(result.array("surplus_to_participants"))
    assert np.array_equal(operator + participants, total)
    assert operator == pytest.approx(0.40 * total)


def test_a_participant_who_leaves_takes_their_account_and_no_surplus():
    """The tabarru' was a donation. A donation that came back on exit would
    not be one, and a surrender value that included a share of the risk
    fund would be paying out of money the remaining participants need."""
    a = assumptions(lapse=0.10)
    result = run(a=a, n=11, outputs=["surrenders", "pols_lapse", "pif"])
    surrenders = np.asarray(result.array("surrenders"))
    lapses = np.asarray(result.array("pols_lapse"))
    pif = np.asarray(result.array("pif"))
    assert np.array_equal(surrenders, lapses * pif)


def test_the_risk_fund_pays_the_sum_covered_and_the_account_pays_itself():
    """Treating the participant's own balance as a claim on the pool is the
    commonest way to make a takaful fund look insolvent when it is not."""
    result = run(n=11, outputs=["death_benefits", "claims_paid",
                                "pif_paid_on_death", "pols_death", "pif"])
    benefits = np.asarray(result.array("death_benefits")).sum(axis=1)
    from_pool = np.asarray(result.array("claims_paid"))[:, 0]
    from_accounts = np.asarray(result.array("pif_paid_on_death"))[:, 0]
    assert benefits == pytest.approx(from_pool + from_accounts)


# --- 6. the executor equivalence class ------------------------------------


def test_the_template_is_recognised_as_pooled():
    pooled = FamilyTakaful.pooled_names()
    for name in ("risk_fund_boy", "qard_drawn", "qard_repaid",
                 "distributable_surplus", "surplus_if_qard_ignored"):
        assert name in pooled, name
    for name in ("tabarru", "pif", "surplus_share_per_pol"):
        assert name not in pooled, name


def test_the_interpreted_executor_refuses_a_pooled_block():
    """RFC-061's block class, asserted rather than assumed. Each of these
    two policies would otherwise see a risk fund containing only itself,
    which is a plausible number and a wrong one."""
    with pytest.raises(PooledBlockError, match="pooled variable"):
        run_interpreted(FamilyTakaful, block(), assumptions(), 11)


def test_a_block_of_one_bridges_into_the_per_policy_class():
    """The bridge RFC-061 defines: a pool of one is the same reduction
    either way, so both executors must agree bitwise on it — which is what
    says the *formulas* meet the full invariant and only the reduction is
    out of the interpreted executor's reach."""
    points = [one()]
    names = sorted(FamilyTakaful.var_names())
    interpreted = run_interpreted(FamilyTakaful, points, assumptions(), 11,
                                  outputs=names)
    vectorized = run_vectorized(FamilyTakaful, points, assumptions(), 11,
                                outputs=names)
    for name in names:
        got = np.array([mp[name] for mp in interpreted.per_mp]).T
        assert np.array_equal(got, np.asarray(vectorized.array(name))), name


def test_a_pooled_block_is_never_chunked_and_the_answer_does_not_move():
    """Chunk-invariance, the other half of what the block class owes. A
    chunked pooled reduction would see part of the population and produce a
    fund balance that depended on the batch size."""
    names = sorted(FamilyTakaful.var_names())
    whole = run_vectorized(FamilyTakaful, block(), assumptions(), 11,
                           outputs=names)
    chunked = run_vectorized(FamilyTakaful, block(), assumptions(), 11,
                             outputs=names, chunk_size=1)
    for name in names:
        assert np.array_equal(np.asarray(whole.array(name)),
                              np.asarray(chunked.array(name))), name


def test_one_scenario_alone_is_its_column_of_the_slab():
    """RFC-068's bridge, and the one this template makes non-trivial: it
    reduces across the *model-point* axis every period, so the assertion is
    that the pooled sum sweeps the block and not the slab."""
    scenarios = ScenarioSet(np.array([[0.10, -0.20, 0.30, 0.05, 0.00, 0.15,
                                       -0.10],
                                      [-0.25, 0.40, 0.10, -0.05, 0.20, 0.00,
                                       0.10]]))
    names = sorted(FamilyTakaful.var_names())
    slab = run_stochastic(FamilyTakaful, block(), assumptions(), scenarios, 6,
                          outputs=names)
    for s in range(scenarios.n_scenarios):
        alone = run_stochastic(FamilyTakaful, block(), assumptions(),
                               scenarios.single(s), 6, outputs=names)
        for name in names:
            assert np.array_equal(np.asarray(slab.array(name))[:, :, s],
                                  np.asarray(alone.array(name))[:, :, 0]), (
                f"scenario {s} var {name}")


def test_the_worked_example_runs_and_exercises_the_loan():
    """A specimen that never drew a qard would demonstrate a with-profits
    fund with different words on it. This asserts the example is calibrated
    to reach the mechanism it exists to show — and it is the scenarios that
    do it, because a deterministic basis gives a fund that is either always
    in surplus or always in deficit."""
    pytest.importorskip("fastapi", reason="needs the [api] extra")
    from engine.api.catalogue import build_run, catalogue
    from engine.api.examples import EXAMPLES

    built = build_run(EXAMPLES["FamilyTakaful"]["request"], catalogue())
    result, record = record_run(**built)
    assert record.executor == "stochastic"
    drawn = np.asarray(result.array("qard_drawn"))[:, 0, :]
    repaid = np.asarray(result.array("qard_repaid"))[:, 0, :]
    assert (drawn.sum(axis=0) > 0).any(), "no path draws a loan"
    assert (repaid.sum(axis=0) > 0).any(), "no path repays one"
    assert (drawn.sum(axis=0) == 0).any(), "every path draws one"


def test_the_model_point_fields_the_template_needs_are_the_ones_it_reads():
    """Guards the worked example against the template growing a field. The
    same check `tests/test_api_demo.py` makes over every example, repeated
    here so a `FamilyTakaful` change fails in the takaful suite too."""
    from engine.core.modeldoc import modelpoint_fields

    fields = modelpoint_fields(FamilyTakaful)
    assert set(fields.required) <= {
        "age_at_entry", "term_years", "sum_covered", "annual_contribution",
        "init_pols",
    }
    assert "initial_pif" in fields.optional
    built = from_dicts([{
        "id": "K1", "age_at_entry": 40, "term_years": 10,
        "sum_covered": 100_000.0, "annual_contribution": 2_000.0,
        "init_pols": 100.0, "initial_pif": 5_000.0,
    }])
    result = run_vectorized(FamilyTakaful, built, assumptions(), 11,
                            outputs=["pif"])
    assert np.asarray(result.array("pif"))[0, 0] == 5_000.0
