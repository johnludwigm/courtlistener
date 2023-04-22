﻿BEGIN;
--
-- Remove trigger snapshot_insert from model audio
--
DROP TRIGGER IF EXISTS pgtrigger_snapshot_insert_43674 ON "audio_audio";
--
-- Remove trigger snapshot_update from model audio
--
DROP TRIGGER IF EXISTS pgtrigger_snapshot_update_c0234 ON "audio_audio";
--
-- Remove trigger snapshot_insert from model audiopanel
--
DROP TRIGGER IF EXISTS pgtrigger_snapshot_insert_0141b ON "audio_audio_panel";
--
-- Remove trigger snapshot_update from model audiopanel
--
DROP TRIGGER IF EXISTS pgtrigger_snapshot_update_17291 ON "audio_audio_panel";
--
-- Create trigger update_or_delete_snapshot_update on model audio
--

CREATE OR REPLACE FUNCTION "public"._pgtrigger_should_ignore(
    trigger_name NAME
)
    RETURNS BOOLEAN AS
$$
DECLARE
    _pgtrigger_ignore TEXT[];
    _result           BOOLEAN;
BEGIN
    BEGIN
        SELECT INTO _pgtrigger_ignore CURRENT_SETTING('pgtrigger.ignore');
    EXCEPTION
        WHEN OTHERS THEN
    END;
    IF _pgtrigger_ignore IS NOT NULL THEN
        SELECT trigger_name = ANY (_pgtrigger_ignore)
        INTO _result;
        RETURN _result;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pgtrigger_update_or_delete_snapshot_update_63362()
    RETURNS TRIGGER AS
$$

BEGIN
    IF ("public"._pgtrigger_should_ignore(TG_NAME) IS TRUE) THEN
        IF (TG_OP = 'DELETE') THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO "audio_audioevent" ("blocked", "case_name", "case_name_full", "case_name_short", "date_blocked",
                                    "date_created", "date_modified", "docket_id", "download_url", "duration",
                                    "filepath_ia", "ia_upload_failure_count", "id", "judges", "local_path_mp3",
                                    "local_path_original_file", "pgh_context_id", "pgh_created_at", "pgh_label",
                                    "pgh_obj_id", "processing_complete", "sha1", "source", "stt_google_response",
                                    "stt_status")
    VALUES (OLD."blocked", OLD."case_name", OLD."case_name_full", OLD."case_name_short", OLD."date_blocked",
            OLD."date_created", OLD."date_modified", OLD."docket_id", OLD."download_url", OLD."duration",
            OLD."filepath_ia", OLD."ia_upload_failure_count", OLD."id", OLD."judges", OLD."local_path_mp3",
            OLD."local_path_original_file", _pgh_attach_context(), NOW(), 'update_or_delete_snapshot', OLD."id",
            OLD."processing_complete", OLD."sha1", OLD."source", OLD."stt_google_response", OLD."stt_status");
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pgtrigger_update_or_delete_snapshot_update_63362 ON "audio_audio";
CREATE TRIGGER pgtrigger_update_or_delete_snapshot_update_63362
    AFTER UPDATE
    ON "audio_audio"


    FOR EACH ROW
    WHEN (OLD."id" IS DISTINCT FROM NEW."id" OR OLD."date_created" IS DISTINCT FROM NEW."date_created" OR
          OLD."docket_id" IS DISTINCT FROM NEW."docket_id" OR OLD."source" IS DISTINCT FROM NEW."source" OR
          OLD."case_name_short" IS DISTINCT FROM NEW."case_name_short" OR
          OLD."case_name" IS DISTINCT FROM NEW."case_name" OR
          OLD."case_name_full" IS DISTINCT FROM NEW."case_name_full" OR OLD."judges" IS DISTINCT FROM NEW."judges" OR
          OLD."sha1" IS DISTINCT FROM NEW."sha1" OR OLD."download_url" IS DISTINCT FROM NEW."download_url" OR
          OLD."local_path_mp3" IS DISTINCT FROM NEW."local_path_mp3" OR
          OLD."local_path_original_file" IS DISTINCT FROM NEW."local_path_original_file" OR
          OLD."filepath_ia" IS DISTINCT FROM NEW."filepath_ia" OR
          OLD."ia_upload_failure_count" IS DISTINCT FROM NEW."ia_upload_failure_count" OR
          OLD."duration" IS DISTINCT FROM NEW."duration" OR
          OLD."processing_complete" IS DISTINCT FROM NEW."processing_complete" OR
          OLD."date_blocked" IS DISTINCT FROM NEW."date_blocked" OR OLD."blocked" IS DISTINCT FROM NEW."blocked" OR
          OLD."stt_status" IS DISTINCT FROM NEW."stt_status" OR
          OLD."stt_google_response" IS DISTINCT FROM NEW."stt_google_response")
EXECUTE PROCEDURE pgtrigger_update_or_delete_snapshot_update_63362();

COMMENT ON TRIGGER pgtrigger_update_or_delete_snapshot_update_63362 ON "audio_audio" IS '7ef321a055255b671442addc460e459a3449f479';
;
--
-- Create trigger update_or_delete_snapshot_delete on model audio
--

CREATE OR REPLACE FUNCTION "public"._pgtrigger_should_ignore(
    trigger_name NAME
)
    RETURNS BOOLEAN AS
$$
DECLARE
    _pgtrigger_ignore TEXT[];
    _result           BOOLEAN;
BEGIN
    BEGIN
        SELECT INTO _pgtrigger_ignore CURRENT_SETTING('pgtrigger.ignore');
    EXCEPTION
        WHEN OTHERS THEN
    END;
    IF _pgtrigger_ignore IS NOT NULL THEN
        SELECT trigger_name = ANY (_pgtrigger_ignore)
        INTO _result;
        RETURN _result;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pgtrigger_update_or_delete_snapshot_delete_63666()
    RETURNS TRIGGER AS
$$

BEGIN
    IF ("public"._pgtrigger_should_ignore(TG_NAME) IS TRUE) THEN
        IF (TG_OP = 'DELETE') THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO "audio_audioevent" ("blocked", "case_name", "case_name_full", "case_name_short", "date_blocked",
                                    "date_created", "date_modified", "docket_id", "download_url", "duration",
                                    "filepath_ia", "ia_upload_failure_count", "id", "judges", "local_path_mp3",
                                    "local_path_original_file", "pgh_context_id", "pgh_created_at", "pgh_label",
                                    "pgh_obj_id", "processing_complete", "sha1", "source", "stt_google_response",
                                    "stt_status")
    VALUES (OLD."blocked", OLD."case_name", OLD."case_name_full", OLD."case_name_short", OLD."date_blocked",
            OLD."date_created", OLD."date_modified", OLD."docket_id", OLD."download_url", OLD."duration",
            OLD."filepath_ia", OLD."ia_upload_failure_count", OLD."id", OLD."judges", OLD."local_path_mp3",
            OLD."local_path_original_file", _pgh_attach_context(), NOW(), 'update_or_delete_snapshot', OLD."id",
            OLD."processing_complete", OLD."sha1", OLD."source", OLD."stt_google_response", OLD."stt_status");
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pgtrigger_update_or_delete_snapshot_delete_63666 ON "audio_audio";
CREATE TRIGGER pgtrigger_update_or_delete_snapshot_delete_63666
    AFTER DELETE
    ON "audio_audio"


    FOR EACH ROW
EXECUTE PROCEDURE pgtrigger_update_or_delete_snapshot_delete_63666();

COMMENT ON TRIGGER pgtrigger_update_or_delete_snapshot_delete_63666 ON "audio_audio" IS '3f15b50d6ff720dceeaf2b1cff5606bf76e31fba';
;
--
-- Create trigger update_or_delete_snapshot_update on model audiopanel
--

CREATE OR REPLACE FUNCTION "public"._pgtrigger_should_ignore(
    trigger_name NAME
)
    RETURNS BOOLEAN AS
$$
DECLARE
    _pgtrigger_ignore TEXT[];
    _result           BOOLEAN;
BEGIN
    BEGIN
        SELECT INTO _pgtrigger_ignore CURRENT_SETTING('pgtrigger.ignore');
    EXCEPTION
        WHEN OTHERS THEN
    END;
    IF _pgtrigger_ignore IS NOT NULL THEN
        SELECT trigger_name = ANY (_pgtrigger_ignore)
        INTO _result;
        RETURN _result;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pgtrigger_update_or_delete_snapshot_update_4e441()
    RETURNS TRIGGER AS
$$

BEGIN
    IF ("public"._pgtrigger_should_ignore(TG_NAME) IS TRUE) THEN
        IF (TG_OP = 'DELETE') THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO "audio_audiopanelevent" ("audio_id", "id", "person_id", "pgh_context_id", "pgh_created_at", "pgh_label")
    VALUES (OLD."audio_id", OLD."id", OLD."person_id", _pgh_attach_context(), NOW(), 'update_or_delete_snapshot');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pgtrigger_update_or_delete_snapshot_update_4e441 ON "audio_audio_panel";
CREATE TRIGGER pgtrigger_update_or_delete_snapshot_update_4e441
    AFTER UPDATE
    ON "audio_audio_panel"


    FOR EACH ROW
    WHEN (OLD.* IS DISTINCT FROM NEW.*)
EXECUTE PROCEDURE pgtrigger_update_or_delete_snapshot_update_4e441();

COMMENT ON TRIGGER pgtrigger_update_or_delete_snapshot_update_4e441 ON "audio_audio_panel" IS '9cadb65943a3d38e84beba67c55089ef7955f87e';
;
--
-- Create trigger update_or_delete_snapshot_delete on model audiopanel
--

CREATE OR REPLACE FUNCTION "public"._pgtrigger_should_ignore(
    trigger_name NAME
)
    RETURNS BOOLEAN AS
$$
DECLARE
    _pgtrigger_ignore TEXT[];
    _result           BOOLEAN;
BEGIN
    BEGIN
        SELECT INTO _pgtrigger_ignore CURRENT_SETTING('pgtrigger.ignore');
    EXCEPTION
        WHEN OTHERS THEN
    END;
    IF _pgtrigger_ignore IS NOT NULL THEN
        SELECT trigger_name = ANY (_pgtrigger_ignore)
        INTO _result;
        RETURN _result;
    ELSE
        RETURN FALSE;
    END IF;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION pgtrigger_update_or_delete_snapshot_delete_f5717()
    RETURNS TRIGGER AS
$$

BEGIN
    IF ("public"._pgtrigger_should_ignore(TG_NAME) IS TRUE) THEN
        IF (TG_OP = 'DELETE') THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END IF;
    INSERT INTO "audio_audiopanelevent" ("audio_id", "id", "person_id", "pgh_context_id", "pgh_created_at", "pgh_label")
    VALUES (OLD."audio_id", OLD."id", OLD."person_id", _pgh_attach_context(), NOW(), 'update_or_delete_snapshot');
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS pgtrigger_update_or_delete_snapshot_delete_f5717 ON "audio_audio_panel";
CREATE TRIGGER pgtrigger_update_or_delete_snapshot_delete_f5717
    AFTER DELETE
    ON "audio_audio_panel"


    FOR EACH ROW
EXECUTE PROCEDURE pgtrigger_update_or_delete_snapshot_delete_f5717();

COMMENT ON TRIGGER pgtrigger_update_or_delete_snapshot_delete_f5717 ON "audio_audio_panel" IS '8281b6cb81fc9e9dcce8dff7fe60a40dc051cd7f';
;
COMMIT;
