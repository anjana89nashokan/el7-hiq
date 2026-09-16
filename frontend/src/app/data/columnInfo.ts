export type DimensionKey =
  | "completeness"
  | "uniqueness"
  | "distribution"
  | "validity"
  | "default";

export const dimensionInfo: Record<DimensionKey, string> = {
  completeness: `
    <p><strong>In Simple Words:</strong> This answers the question: "Is the data actually there?" It checks for empty spots in your dataset, like empty cells in a spreadsheet.</p>
    <p><strong>Why it Matters:</strong> If data is missing, you can't use it. Reports will be wrong, analyses will fail, and you can't contact a customer who has no email address.</p>
    <p><strong>The Formula:</strong> <code>Score = (1 - Percentage of Missing Data) ^ 2</code> (rounded to the nearest integer)</p>
    <ul>
      <li>Find proportion of present data. Example: 10% missing → 90% present (0.9).</li>
      <li>Square it → 0.9 * 0.9 = 0.81 (Score = 81).</li>
      <li>Squaring gives a stronger penalty for early missing data. All final calculated values are rounded to the nearest integer.</li>
    </ul>
    <p><strong>Examples:</strong></p>
    <ul>
      <li>10% missing → Score = 81 (19-point penalty).</li>
      <li>50% missing → Score = 25 (75-point penalty).</li>
    </ul>
  `,

  uniqueness: `
    <p><strong>In Simple Words:</strong> This checks how much of the data is duplicated.</p>
    <p><strong>Why it Matters:</strong> Duplicates inflate numbers and break identity fields like customer_id.</p>
    <p><strong>The Formula:</strong> <code>Score = Percentage of Unique Values</code> (rounded to the nearest integer)</p>
    <ul>
      <li>95% unique → Score = 95.</li>
      <li>10% unique → Score = 10.</li>
    </ul>
  `,

  distribution: `
    <p><strong>In Simple Words:</strong> This checks whether the data is varied or dominated by one repeating value.</p>
    <p><strong>Why it Matters:</strong> A column may be complete but meaningless if 99% values are the same.</p>
    <p><strong>The Formula:</strong> Applies only if the most common value exceeds 50% (all values are rounded to the nearest integer):</p>
    <p><code>Score = 100 - ((MostCommon% - 50) / 50) * 100</code></p>
    <p><strong>Examples:</strong></p>
    <ul>
      <li>60% most common → Score = 80.</li>
      <li>95% most common → Score = 10.</li>
    </ul>
  `,

  validity: `
    <p><strong>In Simple Words:</strong> Checks if the data makes basic common sense.</p>
    <p><strong>Why it Matters:</strong> Detects "garbage" values—negative quantities, meaningless text, etc.</p>
    <h3><strong>Rules:</strong></h3>
    <ul>
      <li><strong>Numeric Columns:</strong><br>
        If min &lt; 0 <strong>and</strong> max &gt; 1,000,000 → <strong>Score = 80</strong><br>
        <em>Indicates mix of real values + error codes.</em>
      </li>
      <li><strong>Text Columns (Avg length = 0):</strong><br>
        Entire column empty → <strong>Score = 30</strong>
      </li>
      <li><strong>Text Columns (0 &lt; Avg length &lt; 2):</strong><br>
        Likely meaningless codes → <strong>Score = 70</strong>
      </li>
    </ul>
  `,

  default: `<p>No information available for this dimension.</p>`
};