// examples/a11y/flutter/a11y_smoke_test.dart
//
// Copy to `test/a11y_smoke_test.dart`, replace `appUnderTest()` with your screen,
// and run `flutter test test/a11y_smoke_test.dart`. Plain `flutter test` — headless,
// no GPU, no extra package.
//
// WHY the two custom guidelines exist (measured on Flutter 3.44.2, and the reason a
// screen-reader defect can ship with every built-in guideline green):
//
//   * `labeledTapTargetGuideline` only checks that a tappable node's label OR tooltip
//     is non-empty. It does NOT look at role, so
//     `GestureDetector(onTap: .., child: Text('送信'))` PASSES while its semantics node
//     reports `isButton == false` — the screen reader never says "button".
//     → ButtonRoleGuideline below closes that hole.
//   * The same guideline accepts ANY non-empty label, so a back button rendered as
//     `Text('<')` PASSES — VoiceOver reads "less than".
//     → MeaningfulLabelGuideline below closes that hole.
//
// Both holes are also caught statically (before the test even runs) by the kit's
// `a11y-static-gate` (A11Y-FLUTTER-ROLE / A11Y-FLUTTER-SYMBOL-LABEL). The static gate
// finds them per source line; these guidelines find them in the rendered semantics
// tree. Keep both: neither subsumes the other.
//
// SDK floor: `flagsCollection` requires Flutter >= 3.32 (it replaced the now-deprecated
// `SemanticsData.hasFlag`). Verified end-to-end on Flutter 3.44.2 / Dart 3.12.2: the two
// custom guidelines FAIL a GestureDetector-with-Icon / Text('<') / unroled-Text screen
// and PASS the corrected one. On Flutter < 3.32 swap `flagsCollection.isButton` for
// `data.hasFlag(SemanticsFlag.isButton)` and drop the Tristate comparisons.
//
// References
//   https://docs.flutter.dev/ui/accessibility/accessibility-testing
//   https://api.flutter.dev/flutter/flutter_test/AccessibilityGuideline-class.html
//   https://docs.flutter.dev/release/breaking-changes/deprecate-textscalefactor
//   https://docs.flutter.dev/release/breaking-changes/android-14-nonlinear-text-scaling-migration

import 'dart:async';
import 'dart:ui' show Tristate;

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/semantics.dart';
import 'package:flutter_test/flutter_test.dart';

// ---------------------------------------------------------------------------
// TODO(project): return the widget you want audited — ideally the real app root,
// or one screen at a time for a large app.
// ---------------------------------------------------------------------------
Widget appUnderTest() => const MaterialApp(home: Placeholder());

/// iOS largest Dynamic Type (AX5). The engine reports body 53pt over the default
/// 17pt, i.e. ~3.12 — NOT 3.0. Testing at 3.0 leaves the top of the range untested.
/// (engine: FlutterViewController.mm `-textScaleFactor`, ax5 = 53, l = 17)
const double kIosAx5TextScale = 53.0 / 17.0; // ≈ 3.1176

/// Android's documented maximum for testing is 200%. Android 14+ scales text
/// non-linearly, so a linear 2.0 is an approximation of the top of the range.
const double kAndroidMaxTextScale = 2.0;

void main() {
  group('a11y — built-in guidelines', () {
    testWidgets('tap target size, labels and text contrast', (tester) async {
      final SemanticsHandle handle = tester.ensureSemantics(); // required, else null assert
      await tester.pumpWidget(appUnderTest());
      await tester.pumpAndSettle();

      // 48x48 (Android) is also what Flutter's own release checklist asks for, so it
      // is the stricter of the two and worth keeping even on an iOS-only app.
      await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
      await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
      await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
      await expectLater(tester, meetsGuideline(textContrastGuideline));

      handle.dispose();
    });

    testWidgets('every tappable node declares a role and a meaningful label',
        (tester) async {
      final SemanticsHandle handle = tester.ensureSemantics();
      await tester.pumpWidget(appUnderTest());
      await tester.pumpAndSettle();

      await expectLater(tester, meetsGuideline(const ButtonRoleGuideline()));
      await expectLater(tester, meetsGuideline(const MeaningfulLabelGuideline()));

      handle.dispose();
    });
  });

  group('a11y — largest text size', () {
    // A RenderFlex overflow is reported through FlutterError inside a debug-mode
    // assert, and `flutter test` runs in debug — so `tester.takeException()` turns
    // "the layout breaks at the largest font size" into a failing test.
    //
    // Measured caveat 1: `MediaQuery.withClampedTextScaling(maxScaleFactor: 1.6)` did
    // NOT fix a fixed-height box (76px overflow became 16px). Clamping is mitigation;
    // making the box grow with its content is the fix. And clamping BELOW 2.0 is itself
    // a WCAG 1.4.4 violation (200% must remain usable), so `maxScaleFactor: 2.0` is the
    // floor — the static gate flags anything lower (A11Y-FLUTTER-SCALE-CLAMP).
    //
    // Measured caveat 2: the overflow assert only exists in debug builds. `flutter test`
    // is debug, so this works — but running the same test with `--release` strips the
    // assert and the check goes quietly green. Never gate on a release-mode run.
    //
    // Measured caveat 3 (the limit of this check): it catches *RenderFlex* overflow —
    // a Column/Row whose children no longer fit — which is the common failure. Text
    // that simply gets clipped inside a fixed `SizedBox` raises no FlutterError and so
    // passes here. Verified: Column-in-a-56px-box fails at 254px (AX5) / 64px (200%),
    // while a bare over-tall Text in a SizedBox does not. Screenshot review at the
    // largest size is still required.
    for (final scale in <String, double>{
      'iOS AX5 (${kIosAx5TextScale.toStringAsFixed(2)}x)': kIosAx5TextScale,
      'Android 200%': kAndroidMaxTextScale,
    }.entries) {
      testWidgets('no overflow at ${scale.key}', (tester) async {
        await tester.pumpWidget(
          MediaQuery(
            data: MediaQueryData(textScaler: TextScaler.linear(scale.value)),
            child: appUnderTest(),
          ),
        );
        await tester.pumpAndSettle();
        expect(
          tester.takeException(),
          isNull,
          reason: 'レイアウトが最大文字サイズで溢れている。固定高さを可変にするか '
              'FittedBox で内容を縮小フィットさせる（クランプは緩和策で修正ではない）',
        );
      });
    }
  });

  group('a11y — reading order of the critical path', () {
    // simulatedAccessibilityTraversal() models the order assistive technology walks
    // the tree. It is an approximation (the docs say so) — it protects the ORDER, it
    // does not replace a device pass with VoiceOver / TalkBack.
    testWidgets('the primary flow is reachable in a sensible order', (tester) async {
      final SemanticsHandle handle = tester.ensureSemantics();
      await tester.pumpWidget(appUnderTest());
      await tester.pumpAndSettle();

      final labels = tester.semantics
          .simulatedAccessibilityTraversal()
          .map((node) => node.label)
          .where((label) => label.isNotEmpty)
          .toList();

      // TODO(project): assert the labels a screen-reader user must hit, in order.
      //   expect(
      //     tester.semantics.simulatedAccessibilityTraversal(),
      //     containsAllInOrder(<Matcher>[
      //       containsSemantics(label: '戻る', isButton: true),
      //       containsSemantics(label: 'メッセージを入力', isTextField: true),
      //       containsSemantics(label: '送信', isButton: true, isEnabled: true),
      //     ]),
      //   );
      expect(labels, isNotEmpty, reason: '読み上げ対象のラベルが 1 つも無い');

      handle.dispose();
    });
  });
}

// ---------------------------------------------------------------------------
// Custom guidelines — the gaps the built-ins leave open
// ---------------------------------------------------------------------------

/// Fails when a semantics node has a tap/long-press action but declares no role.
/// A custom `GestureDetector` button is announced as plain text without this.
class ButtonRoleGuideline extends AccessibilityGuideline {
  const ButtonRoleGuideline();

  @override
  String get description => 'Tappable nodes must declare a role (button/link/textField/…)';

  @override
  FutureOr<Evaluation> evaluate(WidgetTester tester) {
    Evaluation result = const Evaluation.pass();
    for (final RenderView view in tester.binding.renderViews) {
      final SemanticsNode? root = view.owner?.semanticsOwner?.rootSemanticsNode;
      if (root != null) {
        result += _walk(root);
      }
    }
    return result;
  }

  Evaluation _walk(SemanticsNode node) {
    Evaluation result = const Evaluation.pass();
    final SemanticsData data = node.getSemanticsData();
    final bool tappable = data.hasAction(SemanticsAction.tap) ||
        data.hasAction(SemanticsAction.longPress);
    final flags = data.flagsCollection;
    // Verified against the Flutter 3.44.2 SDK: role flags live on flagsCollection as
    // bools, while checked/toggled/selected are Tristate (none = not applicable), and
    // isMergedIntoParent is on the node, not on the flags.
    final bool hasRole = flags.isButton ||
        flags.isLink ||
        flags.isTextField ||
        flags.isSlider ||
        flags.isKeyboardKey ||
        flags.isInMutuallyExclusiveGroup ||
        flags.isToggled != Tristate.none ||
        flags.isSelected != Tristate.none;
    if (tappable && !node.isMergedIntoParent && !flags.isHidden && !hasRole) {
      result += Evaluation.fail(
        '$node: tappable but declares no role. Wrap it in '
        'Semantics(button: true, label: …) or use a real button widget.\n',
      );
    }
    node.visitChildren((SemanticsNode child) {
      result += _walk(child);
      return true;
    });
    return result;
  }
}

/// Fails when an accessible name carries no letters/digits — `'<'`, `'×'`, `'•••'`,
/// `'…'`. The built-in label guideline accepts any non-empty string, so these pass
/// there and then get read out as "less than", "multiplication sign", …
///
/// Whether a label is *understandable* is still a human judgement; this only rules
/// out the labels that provably are not words.
class MeaningfulLabelGuideline extends AccessibilityGuideline {
  const MeaningfulLabelGuideline();

  // Letters (incl. accented), digits, kana, CJK, Hangul. U+00D7 (×) and U+00F7 (÷)
  // are deliberately excluded: they sit inside the Latin-1 letter block but are the
  // most common "close" glyphs.
  static final RegExp _wordish = RegExp(
    r'[0-9A-Za-z'
    r'À-ÖØ-öø-ɏ'
    r'Ѐ-ӿ぀-ゟ゠-ヿ一-鿿가-힯]',
  );

  @override
  String get description => 'Accessible names must contain words, not only symbols';

  @override
  FutureOr<Evaluation> evaluate(WidgetTester tester) {
    Evaluation result = const Evaluation.pass();
    for (final RenderView view in tester.binding.renderViews) {
      final SemanticsNode? root = view.owner?.semanticsOwner?.rootSemanticsNode;
      if (root != null) {
        result += _walk(root);
      }
    }
    return result;
  }

  Evaluation _walk(SemanticsNode node) {
    Evaluation result = const Evaluation.pass();
    final SemanticsData data = node.getSemanticsData();
    final flags = data.flagsCollection;
    final String name = data.label.isNotEmpty ? data.label : data.tooltip;
    final bool interactive = data.hasAction(SemanticsAction.tap) ||
        data.hasAction(SemanticsAction.longPress);
    if (interactive &&
        !node.isMergedIntoParent &&
        !flags.isHidden &&
        name.trim().isNotEmpty &&
        !_wordish.hasMatch(name)) {
      result += Evaluation.fail(
        '$node: accessible name "$name" is symbols only — it will be read as a '
        'symbol name. Give it a word ("戻る"/"閉じる") and hide the glyph with '
        'ExcludeSemantics.\n',
      );
    }
    node.visitChildren((SemanticsNode child) {
      result += _walk(child);
      return true;
    });
    return result;
  }
}
