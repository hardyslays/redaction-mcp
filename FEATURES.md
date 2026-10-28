## Features list:

1. Findings based redaction/replacement service:
    * The inputs should be provided for redaction/replacement service.
    * The input will include a document along with targets to be redacted.
    * The targets can be text, bounding box, regex, etc. The targets can be disabled based on compatibility (e.g.: bounding box redaction doesn't make sense in Data replacement service, as system won't have any reference to generate  synthetic data).
    * Note - The data replacement service is compatible only with Text targets, with optionally additional "entity type" metadata to improve contextual replacement functionality. 

2. Supported file types:
    * PDF files
    * Excel files
    * CSV files
    * DOCX files
    * PPT files

3. Async processing with temporary file storage system:
    * The processing should be asynchronous in nature, with respect for computational limits. 
    * Large files should be temporarilty stored in file storage and processed in chunks, so that the memory doesn't explode while processing the document.
    * The file upload/download should be chunked.

4. Text normalization:
    * Unicode NFKC normalization before matching.
    * Whitespace collapsing so runs of space, tabs or line breaks match normalized quotes.
    * Duplicate findings are normalized away.
    * Longer findings are matched before shorter findings to reduce partial overlap.

5. Boundary-safe matching:
    * Matching uses word-boundary-safe behaviour so short findings do not match inside large words. (For e.g.: john should not be matched with Johnathon)
    * Excel/CSV files should have partial cell matching and redaction/replacement.

6. Shape-preserving replacement:
    * Replacement mode preserves broad visual size and shape of the source token.
    * Lowercase stay lowercase-like.
    * Uppercase stay uppercase-like.
    * Digits stay digits.
    * Whitespace, puncuation and special characters remains same.
    * The replacement mode tries to keep the semantic context of the original text intact. That means a NAME is replaced by a dummy name, Place is replaced by dummy place, and so on.  
    * The above functionality is acheived by either getting the type of target text as input, or trying to figure out the type of target text.

7. Length aware replacement:
    * The replacement values stay close to the length of the original text.
    * Replacements are tuned to remain within a small tolerance.
    * Gereric fitting and truncation logic prevents unrealistic output drift.

8. Entity type aware generators:
    * The synthetic replacement data generated should be generated according to the entity type first to preserve the contextual information of the original text.
    * The entity type list should be created to match as many generic entity types as possible. Some of the example entity types are: NAME, EMAIL, PHONE, SSN, CREDIT CARD NUMBER, ADDRESS, ZIPCODE, DATE, etc.
    * Date formats should be preserved as is.
    * There should be a generic fallback generator mechanism for other entity types. 

9. Multi-line text redaction/replacement:
    * If the target text is present in multiple lines or separated by line-breaks, the redaction and replacement service still work properly. 
    * This is done by text layer based text matching.

10. PDF visual preservations:
    * Original font family is approximated using Basse 14 aliases such as Helvetica, Times, Courier, etc.
    * Bold and italic traits are preserved where detectable.
    * Original font color is preserved.
    * Replacement is placed on the original baseline rather than via a generic textbox flow.
    * Font size is fitted down only as needed and shirnk is capped to reduced visual distortion.

11. PDF background-aware redaction/replacement:
    * Background color is sampled from surrounding pixels inside the target rectangle.
    * Dominant edge-color bucketing is used to pick a fil close to the local page background.
    * White is used as a safe fallback when sampling fails.

12. PDF boundary-safe geometry:
    * Redaction rectangles are inset slightly before applying annotations.
    * This avoids painting over nearby borders and page geometry.
    * Overlap calculations use actual rectangle intersections and area ratios.

13. PDF overlap restoration:
    * The scrubber captures renderable text spans before redaction.
    * It identifies non-target text spans that overlap the redaction rectangle.
    * It distinguishes target-like spans from unrelated overlapping spans.
    * It tracks draw order to prefer restoring only content layerd above the target when possible.
    * It redraws preserved overlapping text after redaction so nearby labels or decorations are not lost.