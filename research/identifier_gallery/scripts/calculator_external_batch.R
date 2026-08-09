args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 5) {
  stop("usage: calculator_external_batch.R INPUT OUTPUT PROVENANCE R_LIBRARY PROGRESS_INTERVAL")
}
input_path <- args[[1]]
output_path <- args[[2]]
provenance_path <- args[[3]]
r_library <- args[[4]]
progress_interval <- as.integer(args[[5]])
if (nzchar(r_library)) .libPaths(c(r_library, .libPaths()))

package_name <- "backgammoncalculator"
requested_ref <- "v0.2.0"
expected_version <- "0.2.0"
expected_commit <- "a385a963ed01a6eac083dae7a1b246b1c150b3eb"

if (!requireNamespace(package_name, quietly = TRUE)) {
  stop("backgammoncalculator is not installed in the configured R library")
}
version <- as.character(utils::packageVersion(package_name))
if (!identical(version, expected_version)) {
  stop(sprintf("expected backgammoncalculator %s, found %s", expected_version, version))
}

installed_path <- system.file(package = package_name)
description <- read.dcf(file.path(installed_path, "DESCRIPTION"))
description_value <- function(name) {
  if (name %in% colnames(description)) description[[1, name]] else ""
}
remote_sha <- description_value("RemoteSha")
remote_ref <- description_value("RemoteRef")
github_sha1 <- description_value("GithubSHA1")
github_ref <- description_value("GithubRef")
sha_metadata <- unique(Filter(nzchar, c(remote_sha, github_sha1)))
if (length(sha_metadata) == 0) {
  stop(sprintf(
    "Calculator provenance lacks immutable commit metadata: expected %s at %s; RemoteRef=%s GithubRef=%s",
    requested_ref, expected_commit, remote_ref, github_ref
  ))
}
if (length(sha_metadata) != 1 || !identical(sha_metadata[[1]], expected_commit)) {
  stop(sprintf(
    "Calculator provenance mismatch: expected %s at %s; RemoteSha=%s RemoteRef=%s GithubSHA1=%s GithubRef=%s",
    requested_ref, expected_commit, remote_sha, remote_ref, github_sha1, github_ref
  ))
}
resolved_sha <- sha_metadata[[1]]

provenance <- data.frame(
  package = package_name,
  package_version = version,
  github_repository = "backgammonsimplified/backgammoncalculator",
  requested_release_ref = requested_ref,
  resolved_release_commit = resolved_sha,
  remote_sha = remote_sha,
  remote_ref = remote_ref,
  github_sha1 = github_sha1,
  github_ref = github_ref,
  installed_path = installed_path,
  rscript = normalizePath(file.path(R.home("bin"), "Rscript.exe"), winslash = "/", mustWork = FALSE),
  stringsAsFactors = FALSE
)
write.csv(provenance, provenance_path, row.names = FALSE, na = "")

input <- read.csv(input_path, stringsAsFactors = FALSE, check.names = FALSE,
                  encoding = "UTF-8")
missing <- setdiff(c("gnuid", "xgid"), names(input))
if (length(missing)) {
  stop(sprintf("input CSV missing required column(s): %s", paste(missing, collapse = ", ")))
}

scalar <- function(value) {
  if (length(value) == 0 || is.null(value) || all(is.na(value))) return("")
  if (is.logical(value)) return(ifelse(value[[1]], "true", "false"))
  as.character(value[[1]])
}

canonical <- function(identifier) {
  tryCatch({
    p <- if (startsWith(identifier, "XGID=")) {
      backgammoncalculator::position_from_xgid(identifier)
    } else {
      backgammoncalculator::position_from_gnuid(identifier)
    }
    list(
      status = "ok",
      error = "",
      player_0_points = paste(p$players$player_0$points, collapse = ";"),
      player_1_points = paste(p$players$player_1$points, collapse = ";"),
      player_0_bar = scalar(p$players$player_0$bar),
      player_1_bar = scalar(p$players$player_1$bar),
      player_0_off = scalar(p$players$player_0$off),
      player_1_off = scalar(p$players$player_1$off),
      on_roll = scalar(p$turn$dice_owner),
      decision_player = scalar(p$turn$turn_owner),
      action = scalar(p$turn$action),
      dice = paste(p$turn$dice, collapse = ";"),
      cube_value = scalar(p$cube$value),
      cube_owner = scalar(p$cube$owner),
      score_player_0 = scalar(p$score[["player_0"]]),
      score_player_1 = scalar(p$score[["player_1"]]),
      match_length = scalar(p$match$length),
      crawford = scalar(p$match$crawford),
      jacoby = scalar(p$match$jacoby),
      beavers = "",
      maximum_cube = scalar(2^p$cube$max_exponent)
    )
  }, error = function(e) {
    empty <- as.list(stats::setNames(rep("", 20), c(
      "player_0_points", "player_1_points", "player_0_bar", "player_1_bar",
      "player_0_off", "player_1_off", "on_roll", "decision_player", "action",
      "dice", "cube_value", "cube_owner", "score_player_0", "score_player_1",
      "match_length", "crawford", "jacoby", "beavers", "maximum_cube", "reserved"
    )))
    empty$reserved <- NULL
    c(list(status = "error", error = conditionMessage(e)), empty)
  })
}

safe_convert <- function(call) {
  tryCatch(
    list(status = "ok", value = as.character(call()), error = ""),
    error = function(e) list(status = "error", value = "", error = conditionMessage(e))
  )
}

prefixed <- function(prefix, values) {
  stats::setNames(values, paste0(prefix, names(values)))
}

empty_fact <- canonical("__invalid_empty_fact_template__")
record_for <- function(index, xgid, gnuid) {
  src_x <- canonical(xgid)
  src_g <- canonical(gnuid)

  x_primary <- safe_convert(function() {
    backgammoncalculator::xgid_to_gnuid(xgid, allow_lossy = TRUE)
  })
  x_mid <- if (x_primary$status == "ok") canonical(x_primary$value) else empty_fact
  x_round <- if (x_primary$status == "ok") {
    safe_convert(function() backgammoncalculator::gnuid_to_xgid(x_primary$value))
  } else {
    list(status = "not_attempted", value = "", error = "")
  }
  x_terminal <- if (x_round$status == "ok") canonical(x_round$value) else empty_fact

  g_primary <- safe_convert(function() backgammoncalculator::gnuid_to_xgid(gnuid))
  g_mid <- if (g_primary$status == "ok") canonical(g_primary$value) else empty_fact
  g_round <- if (g_primary$status == "ok") {
    safe_convert(function() backgammoncalculator::xgid_to_gnuid(g_primary$value, allow_lossy = TRUE))
  } else {
    list(status = "not_attempted", value = "", error = "")
  }
  g_terminal <- if (g_round$status == "ok") canonical(g_round$value) else empty_fact

  c(
    list(
      input_row = as.character(index),
      source_xgid = xgid,
      source_gnuid = gnuid,
      x_primary_status = x_primary$status,
      x_primary_middle = x_primary$value,
      x_primary_error = x_primary$error,
      x_roundtrip_status = x_round$status,
      x_roundtrip_terminal = x_round$value,
      x_roundtrip_error = x_round$error,
      g_primary_status = g_primary$status,
      g_primary_middle = g_primary$value,
      g_primary_error = g_primary$error,
      g_roundtrip_status = g_round$status,
      g_roundtrip_terminal = g_round$value,
      g_roundtrip_error = g_round$error
    ),
    prefixed("src_x__", src_x),
    prefixed("src_g__", src_g),
    prefixed("x_mid__", x_mid),
    prefixed("x_terminal__", x_terminal),
    prefixed("g_mid__", g_mid),
    prefixed("g_terminal__", g_terminal)
  )
}

if (file.exists(output_path)) unlink(output_path)
chunk_size <- 500L
chunk <- list()
written_header <- FALSE
for (i in seq_len(nrow(input))) {
  chunk[[length(chunk) + 1L]] <- record_for(i, input$xgid[[i]], input$gnuid[[i]])
  should_flush <- length(chunk) >= chunk_size || i == nrow(input)
  if (should_flush) {
    frame <- do.call(rbind.data.frame, c(chunk, stringsAsFactors = FALSE))
    write.table(
      frame, output_path, sep = ",", row.names = FALSE,
      col.names = !written_header, append = written_header,
      quote = TRUE, qmethod = "double", na = "", fileEncoding = "UTF-8"
    )
    written_header <- TRUE
    chunk <- list()
  }
  if (progress_interval > 0 && (i %% progress_interval == 0 || i == nrow(input))) {
    cat(sprintf("Calculator reference: %d/%d rows\n", i, nrow(input)))
    flush.console()
  }
}
